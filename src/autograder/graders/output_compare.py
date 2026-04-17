"""Mode 1: Output comparison grader.

Each test case independently checks:
- Return code (optional): zero or nonzero, with separate correct/wrong points
- Stdout (optional): against text or file, with separate correct/wrong points
- Diff options: ignore case, whitespace, whitespace changes, blank lines
"""

from __future__ import annotations

import re
from pathlib import Path

from autograder.graders.base import Grader
from autograder.models import (
    GradeResult,
    GradingMode,
    ItemResult,
    Language,
    ReturnCodeCheck,
    SandboxResult,
    StdinSource,
    StdoutCheck,
    StudentSubmission,
    TestCase,
)
from autograder.sandbox import compile_cpp, get_executable_name, run_student_code


class OutputCompareGrader(Grader):
    def grade(self, submission: StudentSubmission) -> GradeResult:
        items: list[ItemResult] = []

        if not submission.files:
            return GradeResult(
                student_id=submission.student_id,
                mode=GradingMode.OUTPUT_COMPARE,
                error="No submission files found.",
            )

        # For C++: compile once, then run for each test case
        executable = None
        if self.config.language == Language.CPP:
            compile_error, executable = self._compile_cpp(submission)
            if compile_error is not None:
                for tc in self.config.test_cases:
                    items.append(
                        ItemResult(
                            rubric_item=tc.name,
                            points_awarded=0,
                            max_points=tc.max_points,
                            feedback=compile_error,
                        )
                    )
                return GradeResult(
                    student_id=submission.student_id,
                    mode=GradingMode.OUTPUT_COMPARE,
                    items=items,
                )

        for tc in self.config.test_cases:
            # Resolve stdin
            stdin_input = self._resolve_stdin(tc)

            # Build command
            if self.config.language == Language.CPP:
                command = [str(executable)]
            else:
                script = Path(submission.files[0]).resolve()
                command = [self.config.python_command, str(script)]

            # Run
            result = run_student_code(
                command=command,
                cwd=submission.directory,
                stdin_input=stdin_input,
                timeout_seconds=self.config.timeout_seconds,
            )

            if result.timed_out:
                items.append(
                    ItemResult(
                        rubric_item=tc.name,
                        points_awarded=0,
                        max_points=tc.max_points,
                        feedback=f"Timed out after {self.config.timeout_seconds}s.",
                    )
                )
                continue

            # Score this test case
            points, feedback = self._score_test_case(tc, result)
            items.append(
                ItemResult(
                    rubric_item=tc.name,
                    points_awarded=points,
                    max_points=tc.max_points,
                    feedback=feedback,
                )
            )

        return GradeResult(
            student_id=submission.student_id,
            mode=GradingMode.OUTPUT_COMPARE,
            items=items,
        )

    def _resolve_stdin(self, tc: TestCase) -> str:
        """Get the stdin string for a test case."""
        if tc.stdin_source == StdinSource.NONE:
            return ""
        elif tc.stdin_source == StdinSource.TEXT:
            return tc.stdin_text
        elif tc.stdin_source == StdinSource.FILE:
            path = Path(self.assignment_dir) / tc.stdin_file
            return path.read_text(encoding="utf-8")
        return ""

    def _score_test_case(self, tc: TestCase, result: SandboxResult) -> tuple[float, str]:
        """Score a test case based on return code and stdout checks.

        Returns (points_awarded, feedback_string).
        """
        total_points = 0.0
        feedback_parts: list[str] = []

        # --- Return code check ---
        if tc.return_code_check != ReturnCodeCheck.DONT_CHECK:
            rc_pass = False
            if tc.return_code_check == ReturnCodeCheck.ZERO:
                rc_pass = result.return_code == 0
            elif tc.return_code_check == ReturnCodeCheck.NONZERO:
                rc_pass = result.return_code != 0

            if rc_pass:
                total_points += tc.return_code_correct_points
                feedback_parts.append(
                    f"Return code: PASS (got {result.return_code}) "
                    f"[+{tc.return_code_correct_points}]"
                )
            else:
                total_points += tc.return_code_wrong_points
                expected = "0" if tc.return_code_check == ReturnCodeCheck.ZERO else "nonzero"
                feedback_parts.append(
                    f"Return code: FAIL (expected {expected}, got {result.return_code}) "
                    f"[+{tc.return_code_wrong_points}]"
                )
                if result.stderr:
                    feedback_parts.append(f"stderr: {result.stderr[:300]}")

        # --- Stdout check ---
        if tc.stdout_check != StdoutCheck.DONT_CHECK:
            expected_output = self._resolve_expected_output(tc)
            actual_output = result.stdout

            if _compare(actual_output, expected_output, tc):
                total_points += tc.stdout_correct_points
                feedback_parts.append(
                    f"Stdout: PASS [+{tc.stdout_correct_points}]"
                )
            else:
                total_points += tc.stdout_wrong_points
                exp_preview = expected_output.strip()[:200]
                act_preview = actual_output.strip()[:200]
                feedback_parts.append(
                    f"Stdout: FAIL [+{tc.stdout_wrong_points}]\n"
                    f"Expected:\n{exp_preview}\n\n"
                    f"Got:\n{act_preview}"
                )

        return total_points, "\n".join(feedback_parts)

    def _resolve_expected_output(self, tc: TestCase) -> str:
        """Get the expected stdout for a test case."""
        if tc.stdout_check == StdoutCheck.TEXT:
            return tc.stdout_text
        elif tc.stdout_check == StdoutCheck.FILE:
            path = Path(self.assignment_dir) / tc.stdout_file
            return path.read_text(encoding="utf-8")
        return ""

    def _compile_cpp(self, submission: StudentSubmission) -> tuple[str | None, Path | None]:
        """Compile C++ submission. Returns (error_message, executable_path)."""
        sub_dir = Path(submission.directory).resolve()
        exe_name = get_executable_name("solution")
        exe_path = sub_dir / exe_name

        source_files = [str(Path(f).resolve()) for f in submission.files]

        result = compile_cpp(
            source_files=source_files,
            output_path=exe_path,
            cwd=sub_dir,
            compiler=self.config.cpp_compiler,
            flags=self.config.cpp_flags,
            timeout_seconds=self.config.timeout_seconds,
        )

        if result.return_code != 0:
            error = f"Compilation failed:\n{result.stderr[:500]}"
            if result.timed_out:
                error = "Compilation timed out."
            return error, None

        return None, exe_path


def _compare(actual: str, expected: str, tc: TestCase) -> bool:
    """Compare actual vs expected output using the test case's diff options."""
    a = actual
    e = expected

    if tc.ignore_blank_lines:
        a = "\n".join(line for line in a.splitlines() if line.strip())
        e = "\n".join(line for line in e.splitlines() if line.strip())

    if tc.ignore_whitespace:
        # Collapse all whitespace
        a = " ".join(a.split())
        e = " ".join(e.split())
    elif tc.ignore_whitespace_changes:
        # Normalize runs of whitespace to single space, but preserve line structure
        a_lines = [" ".join(line.split()) for line in a.splitlines()]
        e_lines = [" ".join(line.split()) for line in e.splitlines()]
        a = "\n".join(a_lines)
        e = "\n".join(e_lines)
    else:
        # At minimum strip trailing/leading whitespace
        a = a.strip()
        e = e.strip()

    if tc.ignore_case:
        a = a.lower()
        e = e.lower()

    return a == e
