
"""Controlled terminal execution for PHANES."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class TerminalToolError(Exception):
    """Raised when a terminal operation is invalid or unsafe."""


class TerminalTools:
    """Run bounded commands inside a configured workspace."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve(strict=True)

        if not root.is_dir():
            raise ValueError("Workspace root must be a directory.")

        self.workspace_root = root

    def _resolve_cwd(self, cwd: str = ".") -> Path:
        """Resolve a working directory and reject workspace escapes."""
        if not isinstance(cwd, str) or not cwd.strip():
            raise TerminalToolError("Working directory must be a non-empty string.")

        requested = Path(cwd)

        if requested.is_absolute():
            raise TerminalToolError(
                "Absolute working directories are not allowed."
            )

        try:
            resolved = (self.workspace_root / requested).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise TerminalToolError(
                "Working directory does not exist or cannot be resolved."
            ) from exc

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise TerminalToolError(
                "Working directory must remain inside the workspace."
            ) from None

        if not resolved.is_dir():
            raise TerminalToolError("Working directory must be a directory.")

        return resolved

    def run_command(
        self,
        command: list[str],
        cwd: str = ".",
        timeout_seconds: int = 10,
        max_output_chars: int = 10_000,
    ) -> dict[str, Any]:
        """Run a command without a shell and return bounded output."""
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or "\x00" in arg for arg in command)
        ):
            raise TerminalToolError(
                "Command must be a non-empty list of strings without null bytes."
            )

        if not command[0].strip():
            raise TerminalToolError("Command executable cannot be empty.")

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise TerminalToolError("timeout_seconds must be an integer.")

        if not 1 <= timeout_seconds <= 60:
            raise TerminalToolError(
                "timeout_seconds must be between 1 and 60."
            )

        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int):
            raise TerminalToolError("max_output_chars must be an integer.")

        if not 1 <= max_output_chars <= 100_000:
            raise TerminalToolError(
                "max_output_chars must be between 1 and 100,000."
            )

        working_directory = self._resolve_cwd(cwd)

        # Do not inherit API keys or other common secrets into child processes.
        child_env = os.environ.copy()
        for key in list(child_env):
            if key.upper() in {
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY",
            }:
                child_env.pop(key, None)

        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                env=child_env,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""

            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

            return {
                "success": False,
                "return_code": None,
                "stdout": stdout[:max_output_chars],
                "stderr": stderr[:max_output_chars],
                "timed_out": True,
                "error": f"Command timed out after {timeout_seconds} seconds.",
            }
        except OSError:
            return {
                "success": False,
                "return_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "error": "Command could not be started.",
            }

        return {
            "success": completed.returncode == 0,
            "return_code": completed.returncode,
            "stdout": completed.stdout[:max_output_chars],
            "stderr": completed.stderr[:max_output_chars],
            "timed_out": False,
            "error": None,
        }


def register_terminal_tools(
    registry: Any,
    workspace_root: str | Path,
) -> TerminalTools:
    """Register terminal operations in the tool registry."""
    from execution.tool_registry import ToolRegistry

    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry instance.")

    terminal = TerminalTools(workspace_root)
    registry.register("terminal.run_command", terminal.run_command)
    return terminal
