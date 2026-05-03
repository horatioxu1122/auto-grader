"""Sandboxed execution of student code via subprocess.

Uses ``Popen`` directly (not ``subprocess.run``) so we can:
  * place the child in its own session/process group via
    ``start_new_session=True`` — the child does NOT inherit the
    autograder server's controlling TTY;
  * forcibly kill the entire process group on timeout via
    ``os.killpg``, which is reliable even when ``proc.wait()`` would
    otherwise hang on macOS for processes that ended up in the
    foreground TTY group.

The previous ``subprocess.run(input="", timeout=N)`` path was observed
hanging indefinitely on macOS when a student program (e.g., one that
reads from cin into an uninitialized variable) entered an infinite
loop: the kill-on-timeout chain stalled in ``wait()``. Switching to
explicit process-group lifecycle plus ``stdin=DEVNULL`` makes
timeouts reliable.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from autograder.models import SandboxResult

# Hard upper bound to clamp pathological config values. No single
# subprocess call should run longer than this regardless of config.
_HARD_TIMEOUT_CEILING = 300  # 5 minutes

# Cap captured output per stream to keep prompts and CSV rows reasonable.
_MAX_CAPTURE_BYTES = 256 * 1024


def _killpg_quiet(pid: int, sig: int) -> bool:
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, OSError):
        return False


def _drain(stream, buf: list[bytes], cap: int) -> None:
    """Read raw bytes from ``stream`` into ``buf`` until EOF or close.

    Drops anything past ``cap`` to bound memory. Bytes (not text) so a
    SIGKILL on the writer can't leave us stuck mid-decode.
    """
    if stream is None:
        return
    seen = 0
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            if seen < cap:
                room = cap - seen
                buf.append(chunk[:room])
                seen += min(len(chunk), room)
    except Exception:
        return
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_capturing(
    command: list[str],
    cwd: str | Path,
    timeout_seconds: int,
    env: dict[str, str] | None,
    stdin_text: str | None,
) -> SandboxResult:
    """Run ``command`` and return a SandboxResult.

    Uses a watchdog thread + dedicated reader threads instead of
    ``Popen.communicate(timeout=...)``. We observed ``communicate``
    hanging on macOS even after the child died, leaving the server
    process stuck in uninterruptible I/O. The watchdog approach is
    bullet-proof: when the deadline elapses we SIGTERM/SIGKILL the
    entire process group, the kernel closes the child's stdio fds,
    the reader threads see EOF, and ``proc.wait`` returns.

    ``stdin_text``:
      * ``None`` → child gets ``/dev/null`` for stdin (immediate EOF).
      * a string → wired through a pipe; written and closed immediately.
    """
    timeout = max(1, min(int(timeout_seconds), _HARD_TIMEOUT_CEILING))

    popen_kwargs: dict = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        # Bytes mode: avoid blocking inside the text decoder when the
        # child is killed mid-stream.
        "text": False,
        # Detach from the parent's controlling TTY / process group so
        # SIGKILL via killpg can reliably tear the child (and any
        # grand-children) down.
        "start_new_session": True,
    }
    if env is not None:
        popen_kwargs["env"] = env

    if stdin_text is None:
        popen_kwargs["stdin"] = subprocess.DEVNULL
    else:
        popen_kwargs["stdin"] = subprocess.PIPE

    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except FileNotFoundError as e:
        return SandboxResult(stdout="", stderr=str(e), return_code=-1, timed_out=False)
    except Exception as e:
        return SandboxResult(stdout="", stderr=str(e), return_code=-1, timed_out=False)

    out_buf: list[bytes] = []
    err_buf: list[bytes] = []

    out_thread = threading.Thread(
        target=_drain, args=(proc.stdout, out_buf, _MAX_CAPTURE_BYTES), daemon=True
    )
    err_thread = threading.Thread(
        target=_drain, args=(proc.stderr, err_buf, _MAX_CAPTURE_BYTES), daemon=True
    )
    out_thread.start()
    err_thread.start()

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8", errors="replace"))
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass

    timed_out = False
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= deadline:
            timed_out = True
            _killpg_quiet(proc.pid, signal.SIGTERM)
            # Give it ~2s to die from SIGTERM, then escalate.
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
            _killpg_quiet(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Truly unkillable child (kernel-side). Bail out — we
                # leak the zombie but unblock the grading thread.
                pass
            break

    out_thread.join(timeout=2)
    err_thread.join(timeout=2)

    stdout = b"".join(out_buf).decode("utf-8", errors="replace")
    stderr = b"".join(err_buf).decode("utf-8", errors="replace")

    if timed_out:
        msg = f"Process timed out after {timeout} seconds and was killed."
        return SandboxResult(
            stdout=stdout,
            stderr=(stderr + ("\n" if stderr else "") + msg),
            return_code=-1,
            timed_out=True,
        )
    return SandboxResult(
        stdout=stdout,
        stderr=stderr,
        return_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=False,
    )


def run_student_code(
    command: list[str],
    cwd: str | Path,
    stdin_input: str = "",
    timeout_seconds: int = 30,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    """Run a student command with reliable timeout and captured output.

    Empty ``stdin_input`` ⇒ stdin is wired to ``/dev/null`` so reads
    return EOF immediately (cleaner than writing "" to a pipe). Any
    timeout SIGKILLs the whole process group.
    """
    stdin_text = stdin_input if stdin_input else None
    return _run_capturing(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env=env,
        stdin_text=stdin_text,
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

    result = _run_capturing(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env=None,
        stdin_text=None,
    )

    if result.return_code == -1 and not result.timed_out and "No such file or directory" in result.stderr:
        return SandboxResult(
            stdout="",
            stderr=f"Compiler '{compiler}' not found. Make sure g++ is installed and on PATH.",
            return_code=-1,
            timed_out=False,
        )
    if result.timed_out:
        result.stderr = f"Compilation timed out after {timeout_seconds} seconds and was killed."
    return result


def get_executable_name(base_name: str = "solution") -> str:
    """Return the platform-appropriate executable name."""
    if sys.platform == "win32":
        return f"{base_name}.exe"
    return base_name
