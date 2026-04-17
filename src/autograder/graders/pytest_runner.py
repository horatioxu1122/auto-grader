"""Mode 2: Pytest-based grader."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from autograder.graders.base import Grader
from autograder.models import (
    GradeResult,
    GradingMode,
    ItemResult,
    Language,
    PartialCreditMode,
    StudentSubmission,
)
from autograder.sandbox import compile_cpp, get_executable_name, run_student_code


class PytestGrader(Grader):
    def grade(self, submission: StudentSubmission) -> GradeResult:
        if not submission.files:
            return GradeResult(
                student_id=submission.student_id,
                mode=GradingMode.PYTEST,
                error="No submission files found.",
            )

        # Create temp dir with student code + test files
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # For C++: compile student code first
            extra_env: dict[str, str] = {}
            if self.config.language == Language.CPP:
                exe_name = get_executable_name("solution")
                exe_path = tmp_path / exe_name
                source_files = [str(Path(f).resolve()) for f in submission.files]

                comp = compile_cpp(
                    source_files=source_files,
                    output_path=exe_path,
                    cwd=tmp_path,
                    compiler=self.config.cpp_compiler,
                    flags=self.config.cpp_flags,
                    timeout_seconds=self.config.timeout_seconds,
                )

                if comp.return_code != 0:
                    error = f"Compilation failed:\n{comp.stderr[:500]}"
                    if comp.timed_out:
                        error = "Compilation timed out."
                    return GradeResult(
                        student_id=submission.student_id,
                        mode=GradingMode.PYTEST,
                        error=error,
                    )

                # Tests can find the binary via STUDENT_BINARY env var
                extra_env["STUDENT_BINARY"] = str(exe_path)
            else:
                # Copy student Python files into temp dir
                for f in submission.files:
                    src = Path(f)
                    shutil.copy2(src, tmp_path / src.name)

            # Copy test files
            test_src = Path(self.assignment_dir) / self.config.test_dir
            if test_src.exists():
                for test_file in test_src.iterdir():
                    if test_file.is_file():
                        shutil.copy2(test_file, tmp_path / test_file.name)

            # Run pytest with JSON report
            report_file = tmp_path / ".report.json"
            command = [
                self.config.python_command,
                "-m",
                "pytest",
                "--tb=short",
                "-q",
                f"--json-report",
                f"--json-report-file={report_file}",
                str(tmp_path),
            ]

            # Pass environment variables (for C++ binary path)
            import os
            env = {**os.environ, **extra_env}

            result = run_student_code(
                command=command,
                cwd=str(tmp_path),
                timeout_seconds=self.config.timeout_seconds,
                env=env,
            )

            if result.timed_out:
                return GradeResult(
                    student_id=submission.student_id,
                    mode=GradingMode.PYTEST,
                    error=f"Pytest timed out after {self.config.timeout_seconds}s.",
                )

            # Parse JSON report
            if not report_file.exists():
                return GradeResult(
                    student_id=submission.student_id,
                    mode=GradingMode.PYTEST,
                    error=f"Pytest report not generated.\nstderr: {result.stderr[:500]}",
                )

            report = json.loads(report_file.read_text(encoding="utf-8"))
            return self._score_from_report(submission.student_id, report)

    def _score_from_report(self, student_id: str, report: dict) -> GradeResult:
        """Map pytest results to rubric items."""
        # Build a set of passed test node IDs
        tests = report.get("tests", [])
        passed = set()
        failed_details: dict[str, str] = {}

        for test in tests:
            node_id = test.get("nodeid", "")
            # Normalize: strip the temp dir prefix, keep just filename::test_name
            short_id = _short_node_id(node_id)
            if test.get("outcome") == "passed":
                passed.add(short_id)
            else:
                msg = ""
                longrepr = test.get("call", {}).get("longrepr", "")
                if longrepr:
                    msg = str(longrepr)[:300]
                failed_details[short_id] = msg

        items: list[ItemResult] = []

        for rubric_item in self.config.rubric:
            if not rubric_item.tests:
                items.append(
                    ItemResult(
                        rubric_item=rubric_item.name,
                        points_awarded=0,
                        max_points=rubric_item.points,
                        feedback="No tests mapped to this rubric item.",
                    )
                )
                continue

            n_total = len(rubric_item.tests)
            n_passed = sum(1 for t in rubric_item.tests if _short_node_id(t) in passed)

            if self.config.partial_credit == PartialCreditMode.PROPORTIONAL:
                points = rubric_item.points * (n_passed / n_total)
            else:
                points = rubric_item.points if n_passed == n_total else 0

            # Collect failure feedback
            feedback_parts: list[str] = []
            for t in rubric_item.tests:
                short = _short_node_id(t)
                if short not in passed:
                    detail = failed_details.get(short, "")
                    feedback_parts.append(f"FAILED: {t}" + (f"\n{detail}" if detail else ""))

            if n_passed == n_total:
                feedback = "All tests passed."
            else:
                feedback = f"{n_passed}/{n_total} tests passed.\n" + "\n".join(feedback_parts)

            items.append(
                ItemResult(
                    rubric_item=rubric_item.name,
                    points_awarded=round(points, 2),
                    max_points=rubric_item.points,
                    feedback=feedback,
                )
            )

        return GradeResult(
            student_id=student_id,
            mode=GradingMode.PYTEST,
            items=items,
        )


def _short_node_id(node_id: str) -> str:
    """Normalize a pytest node ID to just filename::test_name."""
    # Strip directory prefixes — keep only the last path component
    parts = node_id.replace("\\", "/").split("/")
    return parts[-1] if parts else node_id
