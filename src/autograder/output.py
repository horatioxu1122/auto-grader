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
    """Write a CSV grade report."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect all rubric item names for columns
    all_items: list[str] = []
    for result in results:
        for item in result.items:
            if item.rubric_item not in all_items:
                all_items.append(item.rubric_item)

    fieldnames = ["student_id", *all_items, "total_score", "max_score"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for result in sorted(results, key=lambda r: r.student_id):
            row: dict[str, str | float] = {"student_id": result.student_id}
            for item in result.items:
                row[item.rubric_item] = item.points_awarded
            row["total_score"] = result.total_score
            row["max_score"] = result.max_score
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
