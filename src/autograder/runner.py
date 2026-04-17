"""Orchestrator: load config, discover submissions, dispatch grader, collect results."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from autograder.config import load_config, validate_config
from autograder.discovery import discover_submissions
from autograder.models import AssignmentConfig, GradeResult, GradingMode, StudentSubmission
from autograder.output import write_csv, write_feedback


def get_grader(config: AssignmentConfig, assignment_dir: str):
    """Factory: return the right grader for the mode."""
    if config.mode == GradingMode.OUTPUT_COMPARE:
        from autograder.graders.output_compare import OutputCompareGrader
        return OutputCompareGrader(config, assignment_dir)
    elif config.mode == GradingMode.PYTEST:
        from autograder.graders.pytest_runner import PytestGrader
        return PytestGrader(config, assignment_dir)
    elif config.mode == GradingMode.LLM_REVIEW:
        from autograder.graders.llm_review import LLMReviewGrader
        return LLMReviewGrader(config, assignment_dir)
    else:
        raise ValueError(f"Unknown grading mode: {config.mode}")


def grade_assignment(
    assignment_dir: str | Path,
    submissions_dir: str | Path,
    output_dir: str | Path,
    student_id: str | None = None,
) -> list[GradeResult]:
    """Grade all (or one) student submissions for an assignment."""
    assignment_dir = Path(assignment_dir)
    submissions_dir = Path(submissions_dir)
    output_dir = Path(output_dir)

    config = load_config(assignment_dir)

    # Validate
    issues = validate_config(config, assignment_dir)
    if issues:
        raise ValueError(f"Config validation failed:\n" + "\n".join(f"  - {i}" for i in issues))

    # Discover
    submissions = discover_submissions(
        submissions_dir=submissions_dir,
        pattern=config.submission_pattern,
        student_id=student_id,
    )

    if not submissions:
        raise FileNotFoundError(
            f"No submissions found in {submissions_dir}"
            + (f" for student {student_id}" if student_id else "")
        )

    # Grade
    grader = get_grader(config, str(assignment_dir))
    results: list[GradeResult] = []

    for sub in submissions:
        result = grader.grade(sub)
        results.append(result)

    # Output
    csv_path = output_dir / config.assignment / "grades.csv"
    write_csv(results, config, csv_path)

    # Write feedback files for LLM review mode
    if config.mode == GradingMode.LLM_REVIEW:
        feedback_dir = output_dir / config.assignment / "feedback"
        for result in results:
            write_feedback(result, feedback_dir)

    return results
