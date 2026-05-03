"""Shared data structures for the autograder."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GradingMode(str, Enum):
    OUTPUT_COMPARE = "output_compare"
    PYTEST = "pytest"
    LLM_REVIEW = "llm_review"


class ComparisonMethod(str, Enum):
    EXACT = "exact"
    STRIPPED = "stripped"
    CASE_INSENSITIVE = "case_insensitive"
    FLOAT_TOLERANCE = "float_tolerance"


class StdinSource(str, Enum):
    NONE = "none"
    TEXT = "text"
    FILE = "file"


class ReturnCodeCheck(str, Enum):
    DONT_CHECK = "dont_check"
    ZERO = "zero"
    NONZERO = "nonzero"


class StdoutCheck(str, Enum):
    DONT_CHECK = "dont_check"
    TEXT = "text"
    FILE = "file"


class PartialCreditMode(str, Enum):
    PROPORTIONAL = "proportional"
    ALL_OR_NOTHING = "all_or_nothing"


class GradingStrictness(str, Enum):
    STRICT = "strict"
    MODERATE = "moderate"
    LENIENT = "lenient"


class Language(str, Enum):
    PYTHON = "python"
    CPP = "cpp"


# --- Config models ---


@dataclass
class TestCase:
    name: str
    # Stdin
    stdin_source: StdinSource = StdinSource.NONE
    stdin_text: str = ""
    stdin_file: str = ""
    # Return code
    return_code_check: ReturnCodeCheck = ReturnCodeCheck.DONT_CHECK
    return_code_correct_points: float = 0
    return_code_wrong_points: float = 0
    # Stdout
    stdout_check: StdoutCheck = StdoutCheck.DONT_CHECK
    stdout_text: str = ""
    stdout_file: str = ""
    stdout_correct_points: float = 0
    stdout_wrong_points: float = 0
    # Diff options
    ignore_case: bool = False
    ignore_whitespace: bool = False
    ignore_whitespace_changes: bool = False
    ignore_blank_lines: bool = False

    @property
    def max_points(self) -> float:
        """Maximum possible points for this test case."""
        total = 0.0
        if self.return_code_check != ReturnCodeCheck.DONT_CHECK:
            total += self.return_code_correct_points
        if self.stdout_check != StdoutCheck.DONT_CHECK:
            total += self.stdout_correct_points
        return total

    @property
    def stdin_input(self) -> str:
        """Resolve the actual stdin string to feed to the program."""
        if self.stdin_source == StdinSource.TEXT:
            return self.stdin_text
        # FILE is handled by the grader (reads from assignment dir)
        return ""


@dataclass
class RubricItem:
    name: str
    points: float
    description: str = ""
    tests: list[str] = field(default_factory=list)  # pytest node IDs for mode 2


@dataclass
class AssignmentConfig:
    assignment: str
    mode: GradingMode
    total_points: float
    language: Language = Language.PYTHON
    submission_pattern: str = "*.py"
    timeout_seconds: int = 30
    python_command: str = "python"
    cpp_compiler: str = "g++"
    cpp_flags: list[str] = field(default_factory=lambda: ["-std=c++17", "-O2"])
    # Mode 1
    test_cases: list[TestCase] = field(default_factory=list)
    # Mode 2
    test_dir: str = "tests"
    rubric: list[RubricItem] = field(default_factory=list)
    partial_credit: PartialCreditMode = PartialCreditMode.PROPORTIONAL
    # Mode 3
    instructions_file: str = ""
    rubric_file: str = ""
    model: str = "sonnet"
    max_tokens: int = 4096
    # Optional default stdin to feed when running interactive student
    # programs under LLM Review mode. Empty string ⇒ generic fallback.
    # Lets cin/scanf-based programs run to completion instead of hanging
    # on empty input or hitting uninitialized-variable UB.
    test_stdin: str = ""
    # Grading strictness (affects LLM grading tone)
    strictness: GradingStrictness = GradingStrictness.MODERATE


# --- Runtime models ---


@dataclass
class StudentSubmission:
    student_id: str
    directory: str
    files: list[str] = field(default_factory=list)


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    timed_out: bool = False


@dataclass
class ItemResult:
    rubric_item: str
    points_awarded: float
    max_points: float
    feedback: str = ""


@dataclass
class GradeResult:
    student_id: str
    mode: GradingMode
    items: list[ItemResult] = field(default_factory=list)
    overall_feedback: str = ""
    error: Optional[str] = None

    @property
    def total_score(self) -> float:
        return sum(item.points_awarded for item in self.items)

    @property
    def max_score(self) -> float:
        return sum(item.max_points for item in self.items)
