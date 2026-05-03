"""Generate grade reports (CSV) and per-student feedback files."""

from __future__ import annotations

import csv
from pathlib import Path

from autograder.models import AssignmentConfig, GradeResult


def write_csv(
    results: list[GradeResult],
    config: AssignmentConfig,
    output_path: str | Path,
) -> None:
    """Write a CSV grade report.

    Merges with any existing CSV at output_path so that grading a single
    student (or a subset) preserves previously graded rows. Rows for
    students in the new results replace existing rows by student_id.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    new_ids = {r.student_id for r in results}

    existing_rows: list[dict] = []
    existing_fieldnames: list[str] = []
    if output_path.exists():
        try:
            with open(output_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                existing_fieldnames = list(reader.fieldnames or [])
                for row in reader:
                    if row.get("student_id") not in new_ids:
                        existing_rows.append(row)
        except Exception:
            existing_rows = []
            existing_fieldnames = []

    new_items: list[str] = []
    for result in results:
        for item in result.items:
            if item.rubric_item not in new_items:
                new_items.append(item.rubric_item)

    item_columns: list[str] = []
    for col in existing_fieldnames:
        if col in ("student_id", "total_score", "max_score"):
            continue
        item_columns.append(col)
    for col in new_items:
        if col not in item_columns:
            item_columns.append(col)

    fieldnames = ["student_id", *item_columns, "total_score", "max_score"]

    new_rows: list[dict] = []
    for result in results:
        row: dict[str, str | float] = {"student_id": result.student_id}
        for item in result.items:
            row[item.rubric_item] = item.points_awarded
        row["total_score"] = result.total_score
        row["max_score"] = result.max_score
        new_rows.append(row)

    all_rows = existing_rows + new_rows
    all_rows.sort(key=lambda r: r.get("student_id", ""))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)


def write_feedback(
    result: GradeResult,
    output_dir: str | Path,
) -> None:
    """Write a Markdown feedback file for one student."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    feedback_path = output_dir / f"{result.student_id}.md"

    lines: list[str] = [
        f"# Grading Feedback: {result.student_id}",
        "",
        f"**Total Score: {result.total_score} / {result.max_score}**",
        "",
        "---",
        "",
    ]

    for item in result.items:
        lines.append(f"## {item.rubric_item} ({item.points_awarded}/{item.max_points})")
        lines.append("")
        if item.feedback:
            lines.append(item.feedback)
            lines.append("")

    if result.overall_feedback:
        lines.append("---")
        lines.append("")
        lines.append("## Overall Feedback")
        lines.append("")
        lines.append(result.overall_feedback)
        lines.append("")

    if result.error:
        lines.append("---")
        lines.append("")
        lines.append(f"**Error:** {result.error}")
        lines.append("")

    with open(feedback_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
