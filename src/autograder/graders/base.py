"""Abstract base class for graders."""

from __future__ import annotations

from abc import ABC, abstractmethod

from autograder.models import AssignmentConfig, GradeResult, StudentSubmission


class Grader(ABC):
    def __init__(self, config: AssignmentConfig, assignment_dir: str):
        self.config = config
        self.assignment_dir = assignment_dir

    @abstractmethod
    def grade(self, submission: StudentSubmission) -> GradeResult:
        """Grade a single student submission."""
        ...
