
"""Read-only Git tools for PHANES."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class GitToolError(Exception):
    """Raised when a Git operation is invalid or fails."""


class GitTools:
    """Provide read-only Git inspection tools for a configured repository."""

    def __init__(self, repository_root: str | Path) -> None:
        root = Path(repository_root).expanduser().resolve(strict=True)

        if not root.is_dir():
            raise ValueError("Repository root must be a directory.")

        self.repository_root = root

        result = self._run_git(["rev-parse", "--show-toplevel"])

        if not result["success"]:
            raise ValueError("The configured directory is not a Git repository.")

        actual_root = Path(result["stdout"].strip()).resolve()

        if actual_root != self.repository_root:
            raise ValueError(
                "The configured directory must be the Git repository root."
            )

    def _run_git(
        self,
        arguments: list[str],
        timeout_seconds: int = 10,
    ) -> dict[str, Any]:
        """Run Git without a shell and return bounded diagnostic output."""
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=self.repository_root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": "Git command timed out.",
            }
        except OSError:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": "Git could not be started.",
            }

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:20_000],
            "stderr": result.stderr[:5_000],
            "error": None if result.returncode == 0 else "Git command failed.",
        }

    def status(self) -> dict[str, Any]:
        """Return the current branch and working-tree changes."""
        result = self._run_git(
            ["status", "--short", "--branch", "--untracked-files=normal"]
        )

        if not result["success"]:
            raise GitToolError(result["error"])

        return {"output": result["stdout"]}

    def current_branch(self) -> dict[str, Any]:
        """Return the current local branch name."""
        result = self._run_git(["branch", "--show-current"])

        if not result["success"]:
            raise GitToolError(result["error"])

        branch = result["stdout"].strip()

        return {"branch": branch or None}

    def diff(self, staged: bool = False) -> dict[str, Any]:
        """Return a bounded diff of unstaged or staged changes."""
        if not isinstance(staged, bool):
            raise GitToolError("staged must be a boolean.")

        arguments = ["diff"]

        if staged:
            arguments.append("--cached")

        result = self._run_git(arguments)

        if not result["success"]:
            raise GitToolError(result["error"])

        return {"diff": result["stdout"]}

    def recent_commits(self, limit: int = 10) -> dict[str, Any]:
        """Return recent commit summaries."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise GitToolError("limit must be an integer.")

        if not 1 <= limit <= 50:
            raise GitToolError("limit must be between 1 and 50.")

        result = self._run_git(
            [
                "log",
                f"-{limit}",
                "--date=short",
                "--pretty=format:%h | %ad | %s",
            ]
        )

        if not result["success"]:
            raise GitToolError(result["error"])

        return {"commits": result["stdout"]}


def register_git_tools(
    registry: ToolRegistry,
    repository_root: str | Path,
) -> GitTools:
    """Register read-only Git inspection tools."""
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry instance.")

    git_tools = GitTools(repository_root)

    registry.register(
        "git.status",
        git_tools.status,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "git.current_branch",
        git_tools.current_branch,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "git.diff",
        git_tools.diff,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "git.recent_commits",
        git_tools.recent_commits,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )

    return git_tools
