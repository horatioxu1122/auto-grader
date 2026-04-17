"""Sandboxed execution of student code via subprocess."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from autograder.models import SandboxResult


def run_student_code(
    command: list[str],
    cwd: str | Path,
    stdin_input: str = "",
    timeout_seconds: int = 30,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    """Run a command in a subprocess with timeout and captured output."""
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
        return SandboxResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            stdout="",
            stderr=f"Process timed out after {timeout_seconds} seconds",
            return_code=-1,
            timed_out=True,
        )
    except Exception as e:
        return SandboxResult(
            stdout="",
            stderr=str(e),
            return_code=-1,
            timed_out=False,
        )


def compile_cpp(
    source_files: list[str],
    output_path: str | Path,
    cwd: str | Path,
    compiler: str = "g++",
    flags: list[str] | None = None,
    timeout_seconds: int = 30,
) -> SandboxResult:
    """Compile C++ source file(s) into an executable.

    Returns a SandboxResult where return_code 0 means compilation succeeded.
    """
    if flags is None:
        flags = ["-std=c++17", "-O2"]

    command = [compiler, *flags, "-o", str(output_path), *source_files]

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return SandboxResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            stdout="",
            stderr=f"Compilation timed out after {timeout_seconds} seconds",
            return_code=-1,
            timed_out=True,
        )
    except FileNotFoundError:
        return SandboxResult(
            stdout="",
            stderr=f"Compiler '{compiler}' not found. Make sure g++ is installed and on PATH.",
            return_code=-1,
            timed_out=False,
        )
    except Exception as e:
        return SandboxResult(
            stdout="",
            stderr=str(e),
            return_code=-1,
            timed_out=False,
        )


def get_executable_name(base_name: str = "solution") -> str:
    """Return the platform-appropriate executable name."""
    if sys.platform == "win32":
        return f"{base_name}.exe"
    return base_name
