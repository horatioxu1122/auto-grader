"""Mode 3: LLM-powered comprehension/implementation review via Claude Code.

Workflow:
1. Run the student's code (compile if C++) to get actual execution results
2. Read the source code
3. Send BOTH the source code AND execution output to Claude
4. Claude grades based on real evidence, not guessing
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import nbformat

from autograder.graders.base import Grader
from autograder.models import (
    GradeResult,
    GradingMode,
    GradingStrictness,
    ItemResult,
    Language,
    StudentSubmission,
)
from autograder.sandbox import compile_cpp, get_executable_name, run_student_code

SYSTEM_PROMPT_BASE = """\
You are a fair, consistent university teaching assistant grading student submissions.

You are given:
1. The assignment instructions
2. The grading rubric
3. The student's source code
4. The actual execution output from running the student's code (if applicable)

RULES:
1. Evaluate ONLY against the provided rubric. Do not invent additional criteria.
2. Use the EXECUTION OUTPUT to verify correctness — do not guess whether the code works.
   If the code ran successfully with correct output, that is strong evidence of correctness.
   If the code crashed or produced wrong output, that tells you the code does NOT run correctly,
   but the student may still deserve partial credit for their APPROACH and UNDERSTANDING
   depending on the grading stance below.
3. Give specific line/cell references when deducting points.
4. Be constructive — the student will read this feedback.
5. A perfect submission earns full points. A submission that completely misses a criterion earns 0 for that item.

{strictness_instructions}

You MUST respond with ONLY a JSON object in this exact format (no markdown fences, no extra text):
{{
  "items": [
    {{
      "name": "<rubric item name — must match exactly>",
      "points_awarded": <number>,
      "max_points": <number>,
      "feedback": "<specific feedback for this item>"
    }}
  ],
  "overall_feedback": "<constructive summary for the student>"
}}
"""

STRICTNESS_INSTRUCTIONS = {
    GradingStrictness.STRICT: (
        "GRADING STANCE: STRICT\n"
        "- Award points ONLY when the criterion is clearly and fully met.\n"
        "- Do NOT give benefit of the doubt for unclear, incomplete, or ambiguous work.\n"
        "- Minor issues (poor naming, missing edge cases, incomplete explanation) result in significant deductions.\n"
        "- Partial credit should be rare — the work either meets the bar or it doesn't.\n"
        "- Use the lower end of the scoring range when in doubt.\n"
        "- CRASHED/NON-RUNNING CODE: If the code does not compile or crashes at runtime, "
        "award 0 for any correctness-related rubric items. For approach/methodology items, "
        "award at most 25% if the approach is clearly correct despite the crash."
    ),
    GradingStrictness.MODERATE: (
        "GRADING STANCE: MODERATE\n"
        "- Award points when the criterion is reasonably met, even if imperfect.\n"
        "- Give partial credit for work that demonstrates understanding but has flaws.\n"
        "- Minor stylistic issues should not result in heavy deductions.\n"
        "- Use the full scoring range fairly — neither inflated nor punitive.\n"
        "- CRASHED/NON-RUNNING CODE: If the code does not compile or crashes at runtime, "
        "award 0 for correctness items. For approach/methodology/explanation items, "
        "you may still award up to 50% if the code shows a sound approach that would likely "
        "work with minor fixes (e.g., a typo, missing import, off-by-one). "
        "If the approach itself is fundamentally wrong, award 0."
    ),
    GradingStrictness.LENIENT: (
        "GRADING STANCE: LENIENT\n"
        "- Give benefit of the doubt when the student's intent is clear.\n"
        "- Award partial credit generously for any demonstrated effort or understanding.\n"
        "- Focus deductions on fundamental misunderstandings, not minor issues.\n"
        "- Prioritize encouragement in feedback while still noting areas for improvement.\n"
        "- Use the higher end of the scoring range when the work shows reasonable effort.\n"
        "- CRASHED/NON-RUNNING CODE: If the code does not compile or crashes at runtime, "
        "award 0 for correctness items. For approach/methodology/explanation items, "
        "award up to 75% if the student demonstrates understanding of the right approach. "
        "Even code with bugs shows effort — credit the thinking, note what went wrong, "
        "and suggest how to fix it."
    ),
}


def _build_system_prompt(strictness: GradingStrictness) -> str:
    return SYSTEM_PROMPT_BASE.format(
        strictness_instructions=STRICTNESS_INSTRUCTIONS[strictness]
    )


class LLMReviewGrader(Grader):
    def grade(self, submission: StudentSubmission) -> GradeResult:
        assignment_dir = Path(self.assignment_dir)

        # Read instructions
        instructions_path = assignment_dir / self.config.instructions_file
        instructions = instructions_path.read_text(encoding="utf-8") if instructions_path.exists() else ""

        # Read rubric file
        rubric_path = assignment_dir / self.config.rubric_file
        rubric_text = rubric_path.read_text(encoding="utf-8") if rubric_path.exists() else ""

        # Build rubric YAML summary from config
        rubric_summary = _build_rubric_summary(self.config.rubric)

        # Read student submission
        student_code = _read_submission(submission)

        if not student_code.strip():
            return GradeResult(
                student_id=submission.student_id,
                mode=GradingMode.LLM_REVIEW,
                error="Empty or unreadable submission.",
            )

        # --- EXECUTE the student's code ---
        execution_report = self._execute_submission(submission)

        # Build the user prompt with both code AND execution results
        user_prompt = (
            f"## Assignment Instructions\n\n{instructions}\n\n"
            f"## Grading Rubric\n\n{rubric_text}\n\n"
            f"## Rubric Items (grade each one)\n\n{rubric_summary}\n\n"
            f"## Student Submission (source code)\n\n```\n{student_code}\n```\n\n"
            f"## Execution Results\n\n{execution_report}"
        )

        # Call Claude Code CLI
        system_prompt = _build_system_prompt(self.config.strictness)
        try:
            response = _call_claude_code(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.model,
            )
        except ClaudeCallError as e:
            return GradeResult(
                student_id=submission.student_id,
                mode=GradingMode.LLM_REVIEW,
                error=str(e),
            )

        if response is None:
            return GradeResult(
                student_id=submission.student_id,
                mode=GradingMode.LLM_REVIEW,
                error="Claude returned empty response.",
            )

        # Parse the response
        return _parse_response(submission.student_id, response, self.config.rubric)

    def _execute_submission(self, submission: StudentSubmission) -> str:
        """Run the student's code and return a human-readable execution report.

        This gives Claude real evidence of whether the code works.
        """
        if not submission.files:
            return "No files to execute."

        # Check if this is a notebook — don't execute, but note that outputs are embedded
        has_notebook = any(f.endswith(".ipynb") for f in submission.files)
        if has_notebook:
            return (
                "This is a Jupyter notebook submission. Cell outputs are included "
                "in the source code above. Evaluate based on the embedded outputs."
            )

        parts: list[str] = []

        # Compile if C++
        if self.config.language == Language.CPP:
            sub_dir = Path(submission.directory).resolve()
            exe_name = get_executable_name("solution")
            exe_path = sub_dir / exe_name
            source_files = [str(Path(f).resolve()) for f in submission.files]

            comp = compile_cpp(
                source_files=source_files,
                output_path=exe_path,
                cwd=sub_dir,
                compiler=self.config.cpp_compiler,
                flags=self.config.cpp_flags,
                timeout_seconds=self.config.timeout_seconds,
            )

            if comp.return_code != 0:
                parts.append("### Compilation: FAILED")
                parts.append(f"```\n{comp.stderr[:1000]}\n```")
                return "\n".join(parts)

            parts.append("### Compilation: SUCCESS")
            command = [str(exe_path)]
        else:
            script = Path(submission.files[0]).resolve()
            command = [self.config.python_command, str(script)]

        # Run with no stdin first
        result = run_student_code(
            command=command,
            cwd=submission.directory,
            stdin_input="",
            timeout_seconds=self.config.timeout_seconds,
        )

        if result.timed_out:
            parts.append(f"### Execution: TIMED OUT after {self.config.timeout_seconds}s")
        elif result.return_code != 0:
            parts.append(f"### Execution: RUNTIME ERROR (exit code {result.return_code})")
            if result.stderr:
                parts.append(f"**stderr:**\n```\n{result.stderr[:1000]}\n```")
            if result.stdout:
                parts.append(f"**stdout (partial):**\n```\n{result.stdout[:500]}\n```")
        else:
            parts.append("### Execution: SUCCESS (exit code 0)")
            if result.stdout:
                parts.append(f"**stdout:**\n```\n{result.stdout[:2000]}\n```")
            else:
                parts.append("**stdout:** (empty — no output produced)")
            if result.stderr:
                parts.append(f"**stderr (warnings):**\n```\n{result.stderr[:500]}\n```")

        return "\n\n".join(parts)


def _read_submission(submission: StudentSubmission) -> str:
    """Read all submission files into a single string."""
    parts: list[str] = []
    for filepath in submission.files:
        path = Path(filepath)
        if path.suffix == ".ipynb":
            parts.append(_notebook_to_text(path))
        else:
            try:
                parts.append(f"# --- {path.name} ---\n{path.read_text(encoding='utf-8')}")
            except Exception:
                parts.append(f"# --- {path.name} --- [unreadable]")
    return "\n\n".join(parts)


def _notebook_to_text(path: Path) -> str:
    """Convert a Jupyter notebook to readable text."""
    try:
        nb = nbformat.read(str(path), as_version=4)
    except Exception:
        return f"[Could not parse notebook: {path.name}]"

    parts: list[str] = []
    for i, cell in enumerate(nb.cells):
        cell_type = cell.get("cell_type", "unknown")
        source = cell.get("source", "")
        parts.append(f"# Cell {i + 1} ({cell_type})\n{source}")

        # Include outputs for code cells
        outputs = cell.get("outputs", [])
        for out in outputs:
            if "text" in out:
                parts.append(f"# Output:\n{out['text']}")
            elif "data" in out:
                text_data = out["data"].get("text/plain", "")
                if text_data:
                    parts.append(f"# Output:\n{text_data}")

    return "\n\n".join(parts)


def _build_rubric_summary(rubric_items) -> str:
    """Build a text summary of rubric items for the prompt."""
    lines: list[str] = []
    for item in rubric_items:
        # Use explicit labels so Claude doesn't confuse the name with the point value
        lines.append(f'- name: "{item.name}"')
        lines.append(f"  max_points: {item.points}")
        if item.description:
            lines.append(f"  description: {item.description}")
    return "\n".join(lines)


class ClaudeCallError(Exception):
    """Raised when Claude Code CLI call fails with a specific reason."""
    pass


def _call_claude_code(
    system_prompt: str,
    user_prompt: str,
    model: str = "sonnet",
    max_retries: int = 2,
) -> str | None:
    """Call Claude Code CLI in --print mode with retry logic.

    Retries on transient failures (timeouts, rate limits).
    Fails fast on auth errors or missing CLI.
    Returns the response text, or raises ClaudeCallError with details.
    """
    import os
    import time

    # Remove CLAUDECODE env var so claude CLI doesn't refuse to run
    # when the server was started from inside a Claude Code session
    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                [
                    "claude",
                    "--print",
                    "--model", model,
                    "--system-prompt", system_prompt,
                    user_prompt,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                encoding="utf-8",
                errors="replace",
            )

            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout

            stderr = proc.stderr.strip()

            # Auth errors — don't retry
            if any(kw in stderr.lower() for kw in ["auth", "login", "api key", "unauthorized"]):
                raise ClaudeCallError(
                    f"Authentication failed. Run 'claude' in your terminal to log in.\n"
                    f"Details: {stderr[:300]}"
                )

            # Rate limit — wait and retry
            if any(kw in stderr.lower() for kw in ["rate limit", "too many requests", "429"]):
                wait = attempt * 10  # 10s, 20s
                last_error = f"Rate limited (attempt {attempt}/{max_retries}), waiting {wait}s..."
                time.sleep(wait)
                continue

            # Other non-zero exit
            last_error = stderr[:500] if stderr else f"Exit code {proc.returncode}, empty response"

        except FileNotFoundError:
            raise ClaudeCallError(
                "'claude' command not found. Install Claude Code:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then run 'claude' to authenticate."
            )

        except subprocess.TimeoutExpired:
            last_error = f"Claude timed out after 120s (attempt {attempt}/{max_retries})"
            # Retry on timeout
            continue

        except ClaudeCallError:
            raise

        except Exception as e:
            last_error = str(e)

    # All retries exhausted
    raise ClaudeCallError(f"Claude call failed after {max_retries} attempts. Last error: {last_error}")


def _parse_response(
    student_id: str,
    response: str,
    rubric_items,
) -> GradeResult:
    """Parse Claude's JSON response into a GradeResult."""
    # Try to extract JSON from the response
    json_str = _extract_json(response)
    if json_str is None:
        return GradeResult(
            student_id=student_id,
            mode=GradingMode.LLM_REVIEW,
            error=f"Could not parse LLM response as JSON.\nRaw response:\n{response[:500]}",
        )

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return GradeResult(
            student_id=student_id,
            mode=GradingMode.LLM_REVIEW,
            error=f"Invalid JSON in LLM response.\nExtracted:\n{json_str[:500]}",
        )

    items: list[ItemResult] = []
    # Build a lookup by normalized name (case-insensitive, strip parenthesized suffixes)
    response_items: dict[str, dict] = {}
    for item in data.get("items", []):
        name = item.get("name", "")
        response_items[_normalize_name(name)] = item

    for rubric_item in rubric_items:
        key = _normalize_name(rubric_item.name)
        if key in response_items:
            ri = response_items[key]
            awarded = min(float(ri.get("points_awarded", 0)), rubric_item.points)
            awarded = max(awarded, 0)  # no negative scores
            items.append(
                ItemResult(
                    rubric_item=rubric_item.name,
                    points_awarded=awarded,
                    max_points=rubric_item.points,
                    feedback=ri.get("feedback", ""),
                )
            )
        else:
            items.append(
                ItemResult(
                    rubric_item=rubric_item.name,
                    points_awarded=0,
                    max_points=rubric_item.points,
                    feedback="[LLM did not evaluate this item]",
                )
            )

    return GradeResult(
        student_id=student_id,
        mode=GradingMode.LLM_REVIEW,
        items=items,
        overall_feedback=data.get("overall_feedback", ""),
    )


def _normalize_name(name: str) -> str:
    """Normalize a rubric item name for matching.

    Strips whitespace, lowercases, and removes any parenthesized suffix like
    '(40 points)' so 'Correctness' and 'Correctness (40 points)' compare equal.
    """
    # Remove parenthesized suffix (and everything after)
    result = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    return result.strip().lower()


def _extract_json(text: str) -> str | None:
    """Extract a JSON object from text, handling markdown fences."""
    # Try to find ```json ... ``` block
    match = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try to find raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()

    return None
