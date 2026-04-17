"""FastAPI web interface for the autograder."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from autograder.claude_check import ClaudeStatus, check_claude
from autograder.config import load_config, validate_config
from autograder.discovery import discover_submissions
from autograder.models import GradingMode, GradingStrictness
from autograder.runner import grade_assignment

# Resolve paths relative to the package install location for templates/static,
# and relative to the current working directory for data (assignments, submissions, results).
# This way: templates ship with the package, data lives wherever the TA runs the server.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent  # where templates/static live
_CWD = Path.cwd()

ASSIGNMENTS_DIR = _CWD / "assignments"
SUBMISSIONS_DIR = _CWD / "submissions"
RESULTS_DIR = _CWD / "results"

app = FastAPI(title="Autograder")

templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

# Cache Claude status so we don't re-check on every page load
_claude_status: ClaudeStatus | None = None


@app.on_event("startup")
async def startup_check():
    """Check Claude Code availability on server start."""
    import asyncio

    global _claude_status

    loop = asyncio.get_event_loop()
    _claude_status = await loop.run_in_executor(None, check_claude)

    if _claude_status.ready:
        print(f"[autograder] {_claude_status.summary}")
    else:
        print(f"[autograder] WARNING: {_claude_status.summary}")
        print(f"[autograder] Mode 3 (LLM Review) will not work until this is resolved.")


@app.get("/health")
async def health_check():
    """Check system health including Claude Code status."""
    global _claude_status
    _claude_status = check_claude()  # re-check on demand
    return {
        "claude": {
            "installed": _claude_status.installed,
            "authenticated": _claude_status.authenticated,
            "ready": _claude_status.ready,
            "version": _claude_status.version,
            "summary": _claude_status.summary,
            "error": _claude_status.error,
        },
        "paths": {
            "assignments": str(ASSIGNMENTS_DIR),
            "submissions": str(SUBMISSIONS_DIR),
            "results": str(RESULTS_DIR),
        },
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Dashboard: list all assignments and their status."""
    assignments = []
    if ASSIGNMENTS_DIR.exists():
        for d in sorted(ASSIGNMENTS_DIR.iterdir()):
            if d.is_dir() and (d / "config.yaml").exists():
                config = load_config(d)
                # Count submissions
                sub_dir = SUBMISSIONS_DIR / d.name
                n_submissions = 0
                if sub_dir.exists():
                    n_submissions = sum(1 for s in sub_dir.iterdir() if s.is_dir())
                # Check if graded
                csv_path = RESULTS_DIR / d.name / "grades.csv"
                graded = csv_path.exists()
                assignments.append({
                    "name": d.name,
                    "mode": config.mode.value,
                    "total_points": config.total_points,
                    "n_submissions": n_submissions,
                    "graded": graded,
                })

    return templates.TemplateResponse(request, "index.html", {
        "assignments": assignments,
        "claude_status": _claude_status,
    })


@app.get("/assignment/{name}", response_class=HTMLResponse)
async def assignment_detail(request: Request, name: str):
    """View assignment details, submissions, and grades."""
    assignment_dir = ASSIGNMENTS_DIR / name
    if not assignment_dir.exists():
        return HTMLResponse("Assignment not found", status_code=404)

    config = load_config(assignment_dir)
    issues = validate_config(config, assignment_dir)

    # Submissions
    sub_dir = SUBMISSIONS_DIR / name
    submissions = []
    if sub_dir.exists():
        submissions = discover_submissions(sub_dir, config.submission_pattern)

    # Grades
    grades: list[dict] = []
    csv_path = RESULTS_DIR / name / "grades.csv"
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            grades = list(reader)

    # Feedback files (Mode 3)
    feedback_files: dict[str, str] = {}
    feedback_dir = RESULTS_DIR / name / "feedback"
    if feedback_dir.exists():
        for fb in feedback_dir.glob("*.md"):
            feedback_files[fb.stem] = fb.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "assignment.html", {
        "name": name,
        "config": config,
        "issues": issues,
        "submissions": submissions,
        "grades": grades,
        "feedback_files": feedback_files,
    })


@app.post("/grade/{name}")
async def run_grading(name: str, student: str | None = Form(default=None)):
    """Trigger grading for an assignment."""
    assignment_dir = ASSIGNMENTS_DIR / name
    sub_dir = SUBMISSIONS_DIR / name

    try:
        grade_assignment(
            assignment_dir=assignment_dir,
            submissions_dir=sub_dir,
            output_dir=RESULTS_DIR,
            student_id=student if student else None,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    return RedirectResponse(f"/assignment/{name}", status_code=303)


@app.post("/upload/{name}")
async def upload_submissions(
    name: str,
    files: list[UploadFile] = File(...),
    student_id: str = Form(...),
):
    """Upload submission files for a single student."""
    dest = SUBMISSIONS_DIR / name / student_id
    dest.mkdir(parents=True, exist_ok=True)

    for file in files:
        file_path = dest / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

    return RedirectResponse(f"/assignment/{name}", status_code=303)


@app.post("/upload-bulk/{name}")
async def upload_bulk(
    name: str,
    zipfile_upload: UploadFile = File(...),
    zip_format: str = Form(default="auto"),
    filename_separator: str = Form(default="_"),
):
    """Bulk upload via ZIP. Supports multiple structures:

    - "folders": student_id/files... (multi-file submissions)
    - "flat": files named like studentID_filename.ext (single-file submissions)
    - "auto": detect automatically
    """
    content = await zipfile_upload.read()

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            # Get all real files (skip dirs, __MACOSX, hidden files)
            members = []
            for m in zf.infolist():
                if m.is_dir():
                    continue
                parts = Path(m.filename).parts
                if any(p.startswith("__") or p.startswith(".") for p in parts):
                    continue
                members.append(m)

            if not members:
                return RedirectResponse(f"/assignment/{name}", status_code=303)

            # Auto-detect format
            if zip_format == "auto":
                zip_format = _detect_zip_format(members)

            if zip_format == "folders":
                _extract_folders_zip(zf, members, SUBMISSIONS_DIR / name)
            else:
                _extract_flat_zip(zf, members, SUBMISSIONS_DIR / name, filename_separator)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return RedirectResponse(f"/assignment/{name}", status_code=303)


def _detect_zip_format(members: list[zipfile.ZipInfo]) -> str:
    """Detect whether the ZIP uses folder structure or flat filenames."""
    # If most files are at depth >= 2 (inside subfolders), it's "folders" format
    # If most files are at depth 1 (flat), it's "flat" format
    depths = []
    for m in members:
        parts = Path(m.filename).parts
        depths.append(len(parts))

    avg_depth = sum(depths) / len(depths) if depths else 1
    return "folders" if avg_depth >= 2 else "flat"


def _extract_folders_zip(
    zf: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    dest_base: Path,
) -> None:
    """Extract ZIP with student_id/files... structure."""
    # Check if there's a common wrapper folder to strip
    # e.g., all paths start with "submissions/" — strip that
    all_top_dirs = set()
    for m in members:
        parts = Path(m.filename).parts
        if len(parts) >= 2:
            all_top_dirs.add(parts[0])

    strip_top = len(all_top_dirs) == 1 and all(
        len(Path(m.filename).parts) >= 3 for m in members
    )

    for m in members:
        parts = Path(m.filename).parts
        if strip_top:
            parts = parts[1:]  # remove wrapper dir
        if len(parts) < 2:
            continue

        student_id = parts[0]
        filename = parts[-1]

        dest = dest_base / student_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / filename).write_bytes(zf.read(m.filename))


def _extract_flat_zip(
    zf: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    dest_base: Path,
    separator: str = "_",
) -> None:
    """Extract ZIP with flat filenames like 'studentID_filename.ext'.

    Common LMS patterns:
      - "John_Doe_12345_solution.py" (Canvas)
      - "12345_solution.py"
      - "jdoe_hw1.py"

    Strategy: split on the separator. Everything before the LAST separator-delimited
    segment that looks like a filename (has an extension) becomes the student ID.
    """
    for m in members:
        parts = Path(m.filename).parts
        filename = parts[-1]  # handle any nesting from ZIP

        if separator not in filename:
            # No separator — can't determine student ID, use filename stem
            student_id = Path(filename).stem
            dest = dest_base / student_id
            dest.mkdir(parents=True, exist_ok=True)
            (dest / filename).write_bytes(zf.read(m.filename))
            continue

        # Split and find where the "real filename" starts.
        # Work backwards: the submission filename is the last part with an extension.
        segments = filename.split(separator)

        # Find the split point: scan from right to find where filename begins
        # The filename portion typically has an extension
        split_idx = len(segments) - 1
        for i in range(len(segments) - 1, 0, -1):
            # Check if this segment could be a filename or start of one
            candidate = separator.join(segments[i:])
            if "." in candidate:
                split_idx = i
                break

        student_id = separator.join(segments[:split_idx])
        submission_filename = separator.join(segments[split_idx:])

        if not student_id:
            student_id = Path(submission_filename).stem

        dest = dest_base / student_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / submission_filename).write_bytes(zf.read(m.filename))


# --- Create Assignment ---


@app.get("/create", response_class=HTMLResponse)
async def create_assignment_form(request: Request):
    """Show the create assignment form."""
    return templates.TemplateResponse(request, "create.html", {
        "modes": [
            {"value": "llm_review", "label": "LLM Review (Claude-powered)"},
            {"value": "output_compare", "label": "Output Compare"},
            {"value": "pytest", "label": "Pytest"},
        ],
        "strictness_options": [
            {"value": "strict", "label": "Strict — points only for clearly met criteria"},
            {"value": "moderate", "label": "Moderate — fair partial credit"},
            {"value": "lenient", "label": "Lenient — generous with demonstrated effort"},
        ],
    })


@app.post("/create")
async def create_assignment(
    name: str = Form(...),
    mode: str = Form(...),
    total_points: float = Form(...),
    language: str = Form(default="python"),
    strictness: str = Form(default="moderate"),
    submission_pattern: str = Form(default="*.py"),
    instructions: Optional[UploadFile] = File(default=None),
    rubric_items: str = Form(default=""),
    test_cases_json: str = Form(default="[]"),
):
    """Create a new assignment from the web form."""
    # Sanitize name
    safe_name = name.strip().replace(" ", "_").lower()
    assignment_dir = ASSIGNMENTS_DIR / safe_name

    if assignment_dir.exists():
        return JSONResponse(
            {"error": f"Assignment '{safe_name}' already exists."},
            status_code=400,
        )

    assignment_dir.mkdir(parents=True, exist_ok=True)

    # Build config dict
    config: dict = {
        "assignment": safe_name,
        "mode": mode,
        "language": language,
        "total_points": total_points,
        "submission_pattern": submission_pattern,
        "strictness": strictness,
    }

    if language == "cpp":
        config["cpp_compiler"] = "g++"
        config["cpp_flags"] = ["-std=c++17", "-O2"]

    # Parse rubric items (newline-separated "Name: Points" or "Name: Points - Description")
    rubric_list = _parse_rubric_input(rubric_items, total_points)

    if mode == "llm_review" and (not rubric_items.strip()):
        # Clean up the empty dir we just created
        shutil.rmtree(assignment_dir, ignore_errors=True)
        return JSONResponse(
            {"error": "LLM Review mode requires rubric items. Define at least one criterion."},
            status_code=400,
        )

    if mode == "llm_review":
        config["instructions_file"] = "instructions.md"
        config["rubric_file"] = "rubric.md"
        config["model"] = "sonnet"
        config["max_tokens"] = 4096
        config["rubric"] = rubric_list

        # Save instructions file
        if instructions and instructions.filename:
            instr_content = await instructions.read()
            (assignment_dir / "instructions.md").write_bytes(instr_content)
        else:
            (assignment_dir / "instructions.md").write_text(
                f"# {safe_name}\n\n[Add assignment instructions here]\n",
                encoding="utf-8",
            )

        # Generate rubric.md from rubric items
        rubric_md_lines = [f"# Grading Rubric: {safe_name}\n"]
        for item in rubric_list:
            rubric_md_lines.append(f"## {item['name']} ({item['points']} points)")
            if item.get("description"):
                rubric_md_lines.append(item["description"])
            rubric_md_lines.append("")
        (assignment_dir / "rubric.md").write_text(
            "\n".join(rubric_md_lines), encoding="utf-8"
        )

    elif mode == "pytest":
        config["test_dir"] = "tests"
        config["partial_credit"] = "proportional"
        config["rubric"] = rubric_list
        (assignment_dir / "tests").mkdir(exist_ok=True)

    elif mode == "output_compare":
        # Parse test cases from the dynamic form
        try:
            tc_list = json.loads(test_cases_json)
        except json.JSONDecodeError:
            shutil.rmtree(assignment_dir, ignore_errors=True)
            return JSONResponse({"error": "Invalid test cases data."}, status_code=400)

        if not tc_list:
            shutil.rmtree(assignment_dir, ignore_errors=True)
            return JSONResponse(
                {"error": "Output Compare mode requires at least one test case."},
                status_code=400,
            )

        test_cases = []
        for i, tc in enumerate(tc_list):
            tc_config: dict = {
                "name": tc.get("name", f"Test {i+1}"),
                "stdin_source": tc.get("stdin_source", "none"),
                "stdin_text": tc.get("stdin_text", ""),
                "return_code_check": tc.get("return_code_check", "dont_check"),
                "return_code_correct_points": float(tc.get("return_code_correct_points", 0)),
                "return_code_wrong_points": float(tc.get("return_code_wrong_points", 0)),
                "stdout_check": tc.get("stdout_check", "dont_check"),
                "stdout_correct_points": float(tc.get("stdout_correct_points", 0)),
                "stdout_wrong_points": float(tc.get("stdout_wrong_points", 0)),
                "ignore_case": tc.get("ignore_case", False),
                "ignore_whitespace": tc.get("ignore_whitespace", False),
                "ignore_whitespace_changes": tc.get("ignore_whitespace_changes", False),
                "ignore_blank_lines": tc.get("ignore_blank_lines", False),
            }

            # Handle stdout text — save to file
            stdout_check = tc.get("stdout_check", "dont_check")
            if stdout_check == "text":
                fname = f"expected_{i+1}.txt"
                (assignment_dir / fname).write_text(
                    tc.get("stdout_text", ""), encoding="utf-8"
                )
                tc_config["stdout_check"] = "file"
                tc_config["stdout_file"] = fname

            # Handle stdin text (kept inline in config)
            # Handle stdin file upload would need separate handling

            test_cases.append(tc_config)

        config["test_cases"] = test_cases

    # Write config.yaml
    with open(assignment_dir / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return RedirectResponse(f"/assignment/{safe_name}", status_code=303)


def _parse_rubric_input(raw: str, total_points: float) -> list[dict]:
    """Parse rubric items from form input.

    Accepts newline-separated entries in these formats:
      Name: Points
      Name: Points - Description
    If empty, creates a single item worth total_points.
    """
    if not raw.strip():
        return [{"name": "Overall", "points": total_points, "description": ""}]

    items = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        # Try "Name: Points - Description"
        if ":" in line:
            name_part, rest = line.split(":", 1)
            rest = rest.strip()
            if "-" in rest:
                points_str, desc = rest.split("-", 1)
                points = float(points_str.strip())
                desc = desc.strip()
            else:
                points = float(rest.strip())
                desc = ""
            items.append({
                "name": name_part.strip(),
                "points": points,
                "description": desc,
            })
        else:
            # Fallback: treat whole line as name, distribute points equally
            items.append({"name": line, "points": 0, "description": ""})

    # If any items have 0 points (from fallback), distribute equally
    zero_items = [i for i in items if i["points"] == 0]
    if zero_items:
        assigned = sum(i["points"] for i in items)
        remaining = total_points - assigned
        per_item = remaining / len(zero_items) if zero_items else 0
        for i in zero_items:
            i["points"] = round(per_item, 2)

    return items


@app.get("/submission/{name}/{student_id}", response_class=HTMLResponse)
async def view_submission(request: Request, name: str, student_id: str):
    """View a student's submitted files and grade details."""
    assignment_dir = ASSIGNMENTS_DIR / name
    if not assignment_dir.exists():
        return HTMLResponse("Assignment not found", status_code=404)

    config = load_config(assignment_dir)

    # Read submission files
    sub_dir = SUBMISSIONS_DIR / name / student_id
    if not sub_dir.exists():
        return HTMLResponse("Submission not found", status_code=404)

    files: list[dict] = []
    for filepath in sorted(sub_dir.iterdir()):
        if filepath.is_file() and not filepath.name.startswith("."):
            # Skip compiled executables
            if filepath.suffix in (".exe", ".out", ".o"):
                continue
            try:
                content = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, Exception):
                content = "[Binary or unreadable file]"
            files.append({
                "name": filepath.name,
                "content": content,
                "language": _guess_language(filepath.name),
            })

    # Load grade if available
    grade_row: dict | None = None
    csv_path = RESULTS_DIR / name / "grades.csv"
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("student_id") == student_id:
                    grade_row = row
                    break

    # Load feedback if available
    feedback_content: str | None = None
    feedback_path = RESULTS_DIR / name / "feedback" / f"{student_id}.md"
    if feedback_path.exists():
        feedback_content = feedback_path.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "submission.html", {
        "name": name,
        "student_id": student_id,
        "config": config,
        "files": files,
        "grade": grade_row,
        "feedback": feedback_content,
    })


def _guess_language(filename: str) -> str:
    """Guess syntax highlighting language from file extension."""
    ext_map = {
        ".py": "python",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "cpp",
        ".hpp": "cpp",
        ".java": "java",
        ".js": "javascript",
        ".ipynb": "json",
        ".txt": "text",
        ".md": "markdown",
    }
    ext = Path(filename).suffix.lower()
    return ext_map.get(ext, "text")


@app.get("/feedback/{name}/{student_id}", response_class=HTMLResponse)
async def view_feedback(request: Request, name: str, student_id: str):
    """View detailed feedback for a student (Mode 3)."""
    feedback_path = RESULTS_DIR / name / "feedback" / f"{student_id}.md"
    if not feedback_path.exists():
        return HTMLResponse("Feedback not found", status_code=404)

    content = feedback_path.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "feedback.html", {
        "name": name,
        "student_id": student_id,
        "content": content,
    })
