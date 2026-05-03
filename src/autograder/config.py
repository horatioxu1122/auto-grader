"""Load and validate assignment configurations from YAML."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from autograder.models import (
    AssignmentConfig,
    ComparisonMethod,
    GradingMode,
    GradingStrictness,
    Language,
    PartialCreditMode,
    ReturnCodeCheck,
    RubricItem,
    StdinSource,
    StdoutCheck,
    TestCase,
)


def load_config(assignment_dir: str | Path) -> AssignmentConfig:
    """Load config.yaml from an assignment directory."""
    assignment_dir = Path(assignment_dir)
    config_path = assignment_dir / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"No config.yaml found in {assignment_dir}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mode = GradingMode(raw["mode"])

    language = Language(raw.get("language", "python"))

    config = AssignmentConfig(
        assignment=raw["assignment"],
        mode=mode,
        total_points=float(raw["total_points"]),
        language=language,
        submission_pattern=raw.get("submission_pattern", "*.py" if language == Language.PYTHON else "*.cpp"),
        timeout_seconds=int(raw.get("timeout_seconds", 30)),
        python_command=raw.get("python_command", "python"),
        cpp_compiler=raw.get("cpp_compiler", "g++"),
        cpp_flags=raw.get("cpp_flags", ["-std=c++17", "-O2"]),
    )

    if mode == GradingMode.OUTPUT_COMPARE:
        config.test_cases = _parse_test_cases(raw.get("test_cases", []))

    elif mode == GradingMode.PYTEST:
        config.test_dir = raw.get("test_dir", "tests")
        config.rubric = _parse_rubric(raw.get("rubric", []))
        config.partial_credit = PartialCreditMode(
            raw.get("partial_credit", "proportional")
        )

    elif mode == GradingMode.LLM_REVIEW:
        config.instructions_file = raw.get("instructions_file", "instructions.md")
        config.rubric_file = raw.get("rubric_file", "rubric.md")
        config.rubric = _parse_rubric(raw.get("rubric", []))
        config.model = raw.get("model", "sonnet")
        config.max_tokens = int(raw.get("max_tokens", 4096))
        config.test_stdin = raw.get("test_stdin", "") or ""

    # Strictness applies to all modes but mainly affects LLM grading
    config.strictness = GradingStrictness(raw.get("strictness", "moderate"))

    return config


def validate_config(config: AssignmentConfig, assignment_dir: str | Path) -> list[str]:
    """Validate a config, returning a list of issues (empty = valid)."""
    issues: list[str] = []
    assignment_dir = Path(assignment_dir)

    if config.total_points <= 0:
        issues.append("total_points must be positive")

    if config.mode == GradingMode.OUTPUT_COMPARE:
        if not config.test_cases:
            issues.append("output_compare mode requires at least one test_case")
        points_sum = sum(tc.max_points for tc in config.test_cases)
        if abs(points_sum - config.total_points) > 0.01:
            issues.append(
                f"Test case points sum ({points_sum}) != total_points ({config.total_points})"
            )
        for tc in config.test_cases:
            if tc.stdin_source == StdinSource.FILE:
                stdin_path = assignment_dir / tc.stdin_file
                if not stdin_path.exists():
                    issues.append(f"[{tc.name}] Stdin file not found: {stdin_path}")
            if tc.stdout_check == StdoutCheck.FILE:
                stdout_path = assignment_dir / tc.stdout_file
                if not stdout_path.exists():
                    issues.append(f"[{tc.name}] Expected output file not found: {stdout_path}")
            if tc.return_code_check == ReturnCodeCheck.DONT_CHECK and tc.stdout_check == StdoutCheck.DONT_CHECK:
                issues.append(f"[{tc.name}] Test case checks neither return code nor stdout")

    elif config.mode == GradingMode.PYTEST:
        test_path = assignment_dir / config.test_dir
        if not test_path.exists():
            issues.append(f"Test directory not found: {test_path}")
        if not config.rubric:
            issues.append("pytest mode requires at least one rubric item")

    elif config.mode == GradingMode.LLM_REVIEW:
        instr = assignment_dir / config.instructions_file
        if not instr.exists():
            issues.append(f"Instructions file not found: {instr}")
        rubric = assignment_dir / config.rubric_file
        if not rubric.exists():
            issues.append(f"Rubric file not found: {rubric}")
        if not config.rubric:
            issues.append("llm_review mode requires at least one rubric item")

    return issues


def _parse_test_cases(raw_cases: list[dict]) -> list[TestCase]:
    cases = []
    for tc in raw_cases:
        cases.append(TestCase(
            name=tc["name"],
            # Stdin
            stdin_source=StdinSource(tc.get("stdin_source", "none")),
            stdin_text=tc.get("stdin_text", ""),
            stdin_file=tc.get("stdin_file", ""),
            # Return code
            return_code_check=ReturnCodeCheck(tc.get("return_code_check", "dont_check")),
            return_code_correct_points=float(tc.get("return_code_correct_points", 0)),
            return_code_wrong_points=float(tc.get("return_code_wrong_points", 0)),
            # Stdout
            stdout_check=StdoutCheck(tc.get("stdout_check", "dont_check")),
            stdout_text=tc.get("stdout_text", ""),
            stdout_file=tc.get("stdout_file", ""),
            stdout_correct_points=float(tc.get("stdout_correct_points", 0)),
            stdout_wrong_points=float(tc.get("stdout_wrong_points", 0)),
            # Diff options
            ignore_case=tc.get("ignore_case", False),
            ignore_whitespace=tc.get("ignore_whitespace", False),
            ignore_whitespace_changes=tc.get("ignore_whitespace_changes", False),
            ignore_blank_lines=tc.get("ignore_blank_lines", False),
        ))
    return cases


def _parse_rubric(raw_rubric: list[dict]) -> list[RubricItem]:
    return [
        RubricItem(
            name=item["name"],
            points=float(item["points"]),
            description=item.get("description", ""),
            tests=item.get("tests", []),
        )
        for item in raw_rubric
    ]
