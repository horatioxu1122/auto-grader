"""Verify Claude Code CLI availability and authentication."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


@dataclass
class ClaudeStatus:
    installed: bool = False
    authenticated: bool = False
    version: str = ""
    auth_method: str = ""
    email: str = ""
    subscription: str = ""
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.authenticated

    @property
    def summary(self) -> str:
        if self.ready:
            parts = [f"Claude Code ready (v{self.version})"]
            if self.email:
                parts.append(f"signed in as {self.email}")
            if self.subscription:
                parts.append(f"plan: {self.subscription}")
            return " — ".join(parts)
        if not self.installed:
            return f"Claude Code not found: {self.error}"
        if not self.authenticated:
            return f"Claude Code not authenticated: {self.error}"
        return self.error


def _get_clean_env() -> dict[str, str]:
    """Get env dict with CLAUDECODE removed so CLI doesn't refuse nested sessions."""
    env = {**os.environ}
    env.pop("CLAUDECODE", None)
    return env


def check_claude() -> ClaudeStatus:
    """Check if Claude Code CLI is installed and authenticated.

    Uses 'claude --version' and 'claude auth status' — no real API calls made.
    """
    status = ClaudeStatus()
    env = _get_clean_env()

    # 1. Check if claude is on PATH
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        if proc.returncode == 0:
            status.installed = True
            status.version = proc.stdout.strip()
        else:
            status.error = proc.stderr.strip() or "claude --version returned non-zero"
            return status
    except FileNotFoundError:
        status.error = (
            "'claude' command not found. "
            "Install Claude Code: npm install -g @anthropic-ai/claude-code"
        )
        return status
    except subprocess.TimeoutExpired:
        status.error = "claude --version timed out"
        return status
    except Exception as e:
        status.error = str(e)
        return status

    # 2. Check authentication via 'claude auth status' (no API call)
    try:
        proc = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        if proc.returncode == 0:
            try:
                auth_data = json.loads(proc.stdout)
                if auth_data.get("loggedIn"):
                    status.authenticated = True
                    status.auth_method = auth_data.get("authMethod", "")
                    status.email = auth_data.get("email", "")
                    status.subscription = auth_data.get("subscriptionType", "")
                else:
                    status.error = (
                        "Not logged in. Run 'claude' in your terminal to authenticate."
                    )
            except json.JSONDecodeError:
                # Couldn't parse JSON but command succeeded — might be logged in
                output = proc.stdout.strip().lower()
                if "logged in" in output or "true" in output:
                    status.authenticated = True
                else:
                    status.error = f"Could not parse auth status: {proc.stdout[:200]}"
        else:
            stderr = proc.stderr.strip()
            status.error = (
                f"Auth check failed. Run 'claude' in your terminal to log in.\n"
                f"Details: {stderr[:200]}"
            )
    except subprocess.TimeoutExpired:
        status.error = "Auth status check timed out"
    except Exception as e:
        status.error = str(e)

    return status
