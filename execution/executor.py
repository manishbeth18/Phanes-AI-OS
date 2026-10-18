
"""Execution engine for registered PHANES tools."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from execution.approval import ApprovalManager
from execution.browser import BrowserToolError
from execution.filesystem import FilesystemToolError
from execution.git_tools import GitToolError
from execution.terminal import TerminalToolError
from execution.tool_registry import (
    ToolNotFoundError,
    ToolRegistry,
    ToolSafetyLevel,
)


@dataclass
class ExecutionResult:
    """Structured result returned after a tool execution attempt."""

    tool_name: str
    success: bool
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    verified: bool = False


class Executor:
    """Execute registered tools with registry-enforced safety metadata."""

    def __init__(
        self,
        registry: ToolRegistry,
        approval_manager: ApprovalManager | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry instance.")

        if approval_manager is not None and not isinstance(
            approval_manager, ApprovalManager
        ):
            raise TypeError("approval_manager must be an ApprovalManager instance.")

        self.registry = registry
        self.approval_manager = approval_manager or ApprovalManager()

    @staticmethod
    def _failure(
        tool_name: str,
        code: str,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            tool_name=tool_name,
            success=False,
            error_code=code,
            error_message=message,
            verified=True,
        )

    def approval_required(self, tool_name: str) -> bool:
        """Return whether registry metadata requires approval."""
        metadata = self.registry.get_metadata(tool_name)

        return metadata.safety_level is ToolSafetyLevel.APPROVAL_REQUIRED

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        requires_approval: bool = False,
        approved: bool = False,
        approval_token: str | None = None,
    ) -> ExecutionResult:
        """Execute a registered tool according to its registered safety level.

        Caller-supplied flags can require additional approval but cannot
        downgrade the tool's registered safety level.
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            return self._failure(
                str(tool_name),
                "INVALID_ARGUMENTS",
                "Tool name must be a non-empty string.",
            )

        normalized_name = tool_name.strip()

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return self._failure(
                normalized_name,
                "INVALID_ARGUMENTS",
                "Tool arguments must be a dictionary.",
            )

        if (
            not isinstance(requires_approval, bool)
            or not isinstance(approved, bool)
        ):
            return self._failure(
                normalized_name,
                "INVALID_ARGUMENTS",
                "Approval flags must be Boolean values.",
            )

        try:
            tool = self.registry.get(normalized_name)
            metadata = self.registry.get_metadata(normalized_name)
        except ToolNotFoundError:
            return self._failure(
                normalized_name,
                "TOOL_NOT_FOUND",
                f"Tool '{normalized_name}' is not registered.",
            )
        except (TypeError, ValueError):
            return self._failure(
                normalized_name,
                "INVALID_ARGUMENTS",
                "The tool name is invalid.",
            )

        if metadata.safety_level is ToolSafetyLevel.BLOCKED:
            return self._failure(
                normalized_name,
                "TOOL_BLOCKED",
                "This tool is blocked by its registered safety policy.",
            )

        try:
            signature = inspect.signature(tool)
            signature.bind(**arguments)
        except (TypeError, ValueError):
            return self._failure(
                normalized_name,
                "INVALID_ARGUMENTS",
                "The supplied arguments do not match the tool's signature.",
            )

        approval_needed = (
            metadata.safety_level is ToolSafetyLevel.APPROVAL_REQUIRED
            or requires_approval
        )

        if approval_needed:
            if approval_token is None:
                return self._failure(
                    normalized_name,
                    "APPROVAL_REQUIRED",
                    "This operation requires a valid, action-bound approval token.",
                )

            try:
                valid_approval = self.approval_manager.consume(
                    approval_token,
                    normalized_name,
                    arguments,
                )
            except (TypeError, ValueError):
                valid_approval = False

            if not valid_approval:
                return self._failure(
                    normalized_name,
                    "APPROVAL_INVALID",
                    "The approval is invalid, expired, already used, or for a different action.",
                )

        known_errors: tuple[tuple[type[Exception], str, str], ...] = (
            (
                FilesystemToolError,
                "INTERNAL_ERROR",
                "The filesystem operation failed or was rejected.",
            ),
            (
                TerminalToolError,
                "INVALID_ARGUMENTS",
                "The terminal operation was rejected or failed.",
            ),
            (
                GitToolError,
                "INTERNAL_ERROR",
                "The Git operation failed.",
            ),
            (
                BrowserToolError,
                "PERMISSION_DENIED",
                "The browser request was rejected or failed.",
            ),
        )

        try:
            output = tool(**arguments)
        except Exception as exc:
            for exception_type, error_code, message in known_errors:
                if isinstance(exc, exception_type):
                    return self._failure(
                        normalized_name,
                        error_code,
                        message,
                    )

            return self._failure(
                normalized_name,
                "INTERNAL_ERROR",
                "The tool failed during execution.",
            )

        return ExecutionResult(
            tool_name=normalized_name,
            success=True,
            output=output,
            verified=False,
        )
