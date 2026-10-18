
"""Automatic safety policy for PHANES tool execution."""

from __future__ import annotations

import re
from typing import Any


class SafetyPolicy:
    """Require approval unless a tool call is explicitly read-only."""

    _READ_ONLY_OPERATIONS = {
        "read",
        "read_file",
        "read_text",
        "read_text_file",
        "list",
        "list_files",
        "list_directory",
        "list_directories",
        "search",
        "search_files",
        "find",
        "exists",
        "stat",
        "info",
        "status",
        "current_branch",
        "branch",
        "diff",
        "recent_commits",
        "log",
        "fetch",
        "fetch_page",
        "get_page",
    }

    _MUTATING_WORDS = {
        "write", "create", "update", "edit", "modify", "delete",
        "remove", "move", "rename", "copy", "replace", "append",
        "overwrite", "install", "uninstall", "execute", "run",
        "shell", "command", "commit", "push", "pull", "merge",
        "checkout", "reset", "clean", "rebase", "kill", "shutdown",
        "restart",
    }

    _READ_ONLY_NAMESPACES = {"filesystem", "git", "browser"}

    @staticmethod
    def _parts(name: str) -> set[str]:
        return {
            part
            for part in re.split(r"[^a-z0-9]+", name.lower())
            if part
        }

    def requires_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        """Fail closed unless the tool is explicitly recognized as read-only."""
        if not isinstance(tool_name, str) or not tool_name.strip():
            return True

        if not isinstance(arguments, dict):
            return True

        name = tool_name.strip().lower()
        parts = self._parts(name)

        # Arbitrary terminal/shell execution always needs approval.
        if "terminal" in parts or "shell" in parts:
            return True

        # Any recognized mutating/consequential word requires approval.
        if parts.intersection(self._MUTATING_WORDS):
            return True

        # Require a recognized namespace and an exact operation name.
        if "." not in name:
            return True

        namespace, operation = name.split(".", 1)

        if namespace not in self._READ_ONLY_NAMESPACES:
            return True

        if operation not in self._READ_ONLY_OPERATIONS:
            return True

        return False
