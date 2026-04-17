"""CLI entry point for the autograder."""

from __future__ import annotations

from pathlib import Path

import click

from autograder.config import load_config, validate_config
from autograder.runner import grade_assignment


@click.group()
def main():
    """Autograder — multi-mode assignment grading tool."""
    pass


@main.command()
@click.argument("assignment")
@click.option("--assignments-dir", default="assignments", help="Base directory for assignments.")
@click.option("--submissions-dir", default="submissions", help="Base directory for submissions.")
@click.option("--output-dir", default="results", help="Output directory for grades.")
@click.option("--student", default=None, help="Grade only this student ID.")
def grade(assignment, assignments_dir, submissions_dir, output_dir, student):
    """Grade all submissions for ASSIGNMENT."""
    assignment_dir = Path(assignments_dir) / assignment
    sub_dir = Path(submissions_dir) / assignment
    out_dir = Path(output_dir)

    if not assignment_dir.exists():
        click.echo(f"Error: Assignment directory not found: {assignment_dir}", err=True)
        raise SystemExit(1)

    try:
        results = grade_assignment(
            assignment_dir=assignment_dir,
            submissions_dir=sub_dir,
            output_dir=out_dir,
            student_id=student,
        )
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    # Summary
    click.echo(f"\nGraded {len(results)} submission(s) for '{assignment}':")
    for r in sorted(results, key=lambda r: r.student_id):
        status = f"{r.total_score}/{r.max_score}"
        if r.error:
            status = f"ERROR: {r.error[:60]}"
        click.echo(f"  {r.student_id}: {status}")

    csv_path = out_dir / assignment / "grades.csv"
    click.echo(f"\nGrades written to: {csv_path}")


@main.command()
@click.argument("assignment")
@click.option("--assignments-dir", default="assignments", help="Base directory for assignments.")
def validate(assignment, assignments_dir):
    """Validate the config for ASSIGNMENT without grading."""
    assignment_dir = Path(assignments_dir) / assignment

    if not assignment_dir.exists():
        click.echo(f"Error: Assignment directory not found: {assignment_dir}", err=True)
        raise SystemExit(1)

    try:
        config = load_config(assignment_dir)
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
        raise SystemExit(1)

    issues = validate_config(config, assignment_dir)

    if issues:
        click.echo(f"Validation FAILED for '{assignment}':")
        for issue in issues:
            click.echo(f"  - {issue}")
        raise SystemExit(1)
    else:
        click.echo(f"Config for '{assignment}' is valid.")
        click.echo(f"  Mode: {config.mode.value}")
        click.echo(f"  Total points: {config.total_points}")
        if config.rubric:
            click.echo(f"  Rubric items: {len(config.rubric)}")
        if config.test_cases:
            click.echo(f"  Test cases: {len(config.test_cases)}")


@main.command()
@click.option("--port", default=8000, help="Port to run on.")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
def serve(port, host):
    """Start the autograder web interface."""
    import uvicorn

    click.echo(f"Starting autograder at http://{host}:{port}")
    uvicorn.run("autograder.web:app", host=host, port=port)


@main.command()
def status():
    """Check Claude Code authentication status."""
    from autograder.claude_check import check_claude

    s = check_claude()
    if s.ready:
        click.echo(s.summary)
    else:
        click.echo(f"NOT READY: {s.summary}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
