"""Orchestrator: load config, discover submissions, dispatch grader, collect results."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from autograder.config import load_config, validate_config
from autograder.discovery import discover_submissions
from autograder.models import AssignmentConfig, GradeResult, GradingMode, StudentSubmission
from autograder.output import write_csv, write_feedback


# Progress event shape (a plain dict so it serializes cleanly to JSON):
#   { "phase": "starting"|"grading"|"finished", "current": str|None,
#     "completed": int, "total": int,
#     "last_result": {"student_id","score","max","error","elapsed_seconds"}? }
ProgressCallback = Callable[[dict], None]


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
    progress_callback: ProgressCallback | None = None,
) -> list[GradeResult]:
    """Grade all (or one) student submissions for an assignment.

    Writes per-student results to disk inside the loop so a partial run
    (interrupted server, hung LLM call, browser disconnect) still
    persists completed students. ``progress_callback`` is invoked
    before and after each student so callers can surface live progress.
    """
    assignment_dir = Path(assignment_dir)
    submissions_dir = Path(submissions_dir)
    output_dir = Path(output_dir)

    config = load_config(assignment_dir)

    issues = validate_config(config, assignment_dir)
    if issues:
        raise ValueError(f"Config validation failed:\n" + "\n".join(f"  - {i}" for i in issues))

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

    grader = get_grader(config, str(assignment_dir))
    results: list[GradeResult] = []

    total = len(submissions)
    _log(f"Grading {config.assignment} ({total} submission{'s' if total != 1 else ''}) — mode: {config.mode.value}")
    overall_start = time.time()

    csv_path = output_dir / config.assignment / "grades.csv"
    feedback_dir = output_dir / config.assignment / "feedback"

    def _emit(event: dict) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(event)
        except Exception as cb_err:
            _log(f"progress callback raised: {cb_err}")

    _emit({"phase": "starting", "completed": 0, "total": total, "current": None})

    for i, sub in enumerate(submissions, start=1):
        _emit({
            "phase": "grading",
            "current": sub.student_id,
            "completed": i - 1,
            "total": total,
        })

        start = time.time()
        result = grader.grade(sub)
        elapsed = time.time() - start
        results.append(result)

        if result.error:
            _log(f"[{i}/{total}] {sub.student_id} — ERROR ({elapsed:.1f}s): {result.error[:80]}")
        else:
            _log(f"[{i}/{total}] {sub.student_id} — {result.total_score:.1f}/{result.max_score:.1f} ({elapsed:.1f}s)")

        # Persist this single result immediately. write_csv merges with
        # the existing CSV by student_id, so partial runs are safe.
        try:
            write_csv([result], config, csv_path)
        except Exception as e:
            _log(f"  (failed to write CSV row for {sub.student_id}: {e})")

        if config.mode == GradingMode.LLM_REVIEW:
            try:
                write_feedback(result, feedback_dir)
            except Exception as e:
                _log(f"  (failed to write feedback for {sub.student_id}: {e})")

        _emit({
            "phase": "grading",
            "current": sub.student_id,
            "completed": i,
            "total": total,
            "last_result": {
                "student_id": result.student_id,
                "score": result.total_score,
                "max": result.max_score,
                "error": result.error,
                "elapsed_seconds": round(elapsed, 1),
            },
        })

    total_elapsed = time.time() - overall_start
    _log(f"Done in {total_elapsed:.1f}s")

    _emit({
        "phase": "finished",
        "completed": total,
        "total": total,
        "current": None,
        "elapsed_seconds": round(total_elapsed, 1),
    })

    return results


def _log(msg: str) -> None:
    """Print a progress line to the server's terminal."""
    print(f"[autograder] {msg}", flush=True)
