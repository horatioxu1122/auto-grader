"""Discover student submissions for an assignment."""

from __future__ import annotations

import glob
from pathlib import Path

from autograder.models import StudentSubmission


def discover_submissions(
    submissions_dir: str | Path,
    pattern: str = "*.py",
    student_id: str | None = None,
) -> list[StudentSubmission]:
    """Find all student submissions in the given directory.

    Each subdirectory of submissions_dir is treated as one student.
    The subdirectory name is the student ID.
    """
    submissions_dir = Path(submissions_dir)

    if not submissions_dir.exists():
        raise FileNotFoundError(f"Submissions directory not found: {submissions_dir}")

    submissions: list[StudentSubmission] = []

    for student_dir in sorted(submissions_dir.iterdir()):
        if not student_dir.is_dir():
            continue
        if student_id and student_dir.name != student_id:
            continue

        files = sorted(
            str(p) for p in student_dir.glob(pattern) if p.is_file()
        )
        if not files:
            files = sorted(
                str(p)
                for p in student_dir.rglob(pattern)
                if p.is_file()
            )

        submissions.append(
            StudentSubmission(
                student_id=student_dir.name,
                directory=str(student_dir),
                files=files,
            )
        )

    return submissions
