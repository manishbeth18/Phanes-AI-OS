
"""Execution engine for registered PHANES tools."""

from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

from shared.contracts import ErrorCode, ExecutionStatus, ToolError, ToolResult

from execution.approval import ApprovalManager
from execution.browser import BrowserToolError
from execution.filesystem import FilesystemToolError
from execution.git_tools import GitToolError
from execution.safety_policy import SafetyPolicy
from execution.terminal import TerminalToolError
from execution.tool_registry import (
    ToolNotFoundError,
    ToolRegistry,
    ToolSafetyLevel,
)


class Executor:
    """Execute registered tools using canonical PHANES result contracts."""

    _POLICY_ENFORCED_NAMESPACES = {
        "filesystem",
        "git",
        "browser",
        "terminal",
        "shell",
    }

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
        self.safety_policy = SafetyPolicy()

    @staticmethod
    def _failure(
        tool_name: str,
        error_code: ErrorCode,
        message: str,
        *,
        task_id: str,
        step_id: str,
        retryable: bool = False,
    ) -> ToolResult:
        return ToolResult(
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            status=ExecutionStatus.FAILED,
            success=False,
            error=ToolError(
                code=error_code,
                message=message,
                retryable=retryable,
                details={"tool_name": tool_name},
            ),
            verified=False,
        )

    def approval_required(self, tool_name: str) -> bool:
        """Return whether registry metadata or safety policy requires approval."""
        metadata = self.registry.get_metadata(tool_name)
        namespace = tool_name.split(".", 1)[0].lower()

        policy_requires_approval = (
            namespace in self._POLICY_ENFORCED_NAMESPACES
            and self.safety_policy.requires_approval(tool_name, {})
        )

        return (
            metadata.safety_level is ToolSafetyLevel.APPROVAL_REQUIRED
            or policy_requires_approval
        )

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        requires_approval: bool = False,
        approved: bool = False,
        approval_token: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> ToolResult:
        """Execute a tool and return a canonical ToolResult.

        Callers coordinating a plan should supply task_id and step_id.
        Standalone calls receive generated identifiers.

        The legacy ``approved`` flag is accepted for compatibility but never
        authorizes an operation. Approval-required tools need a valid,
        single-use token bound to the exact tool and arguments.
        """
        resolved_task_id = (
            task_id
            if isinstance(task_id, str) and task_id.strip()
            else uuid4().hex
        )
        resolved_step_id = (
            step_id
            if isinstance(step_id, str) and step_id.strip()
            else uuid4().hex
        )
        display_name = (
            tool_name.strip() if isinstance(tool_name, str) else str(tool_name)
        )

        if (
            not isinstance(task_id, (str, type(None)))
            or (isinstance(task_id, str) and not task_id.strip())
            or not isinstance(step_id, (str, type(None)))
            or (isinstance(step_id, str) and not step_id.strip())
        ):
            return self._failure(
                display_name,
                ErrorCode.INVALID_ARGUMENTS,
                "Task and step identifiers must be non-empty strings when supplied.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        if not isinstance(tool_name, str) or not tool_name.strip():
            return self._failure(
                display_name,
                ErrorCode.INVALID_ARGUMENTS,
                "Tool name must be a non-empty string.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        normalized_name = tool_name.strip()

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return self._failure(
                normalized_name,
                ErrorCode.INVALID_ARGUMENTS,
                "Tool arguments must be a dictionary.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        if not isinstance(requires_approval, bool) or not isinstance(approved, bool):
            return self._failure(
                normalized_name,
                ErrorCode.INVALID_ARGUMENTS,
                "Approval flags must be Boolean values.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        if approval_token is not None and not isinstance(approval_token, str):
            return self._failure(
                normalized_name,
                ErrorCode.INVALID_ARGUMENTS,
                "Approval token must be a string when supplied.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        try:
            tool = self.registry.get(normalized_name)
            metadata = self.registry.get_metadata(normalized_name)
        except ToolNotFoundError:
            return self._failure(
                normalized_name,
                ErrorCode.TOOL_NOT_FOUND,
                f"Tool '{normalized_name}' is not registered.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )
        except (TypeError, ValueError):
            return self._failure(
                normalized_name,
                ErrorCode.INVALID_ARGUMENTS,
                "The tool name is invalid.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        if metadata.safety_level is ToolSafetyLevel.BLOCKED:
            return self._failure(
                normalized_name,
                ErrorCode.TOOL_BLOCKED,
                "This tool is blocked by its registered safety policy.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        try:
            inspect.signature(tool).bind(**arguments)
        except (TypeError, ValueError):
            return self._failure(
                normalized_name,
                ErrorCode.INVALID_ARGUMENTS,
                "The supplied arguments do not match the tool's signature.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        namespace = normalized_name.split(".", 1)[0].lower()
        policy_requires_approval = (
            namespace in self._POLICY_ENFORCED_NAMESPACES
            and self.safety_policy.requires_approval(normalized_name, arguments)
        )

        approval_needed = (
            metadata.safety_level is ToolSafetyLevel.APPROVAL_REQUIRED
            or requires_approval
            or policy_requires_approval
        )

        if approval_needed:
            if approval_token is None:
                return self._failure(
                    normalized_name,
                    ErrorCode.APPROVAL_REQUIRED,
                    "This operation requires a valid, action-bound approval token.",
                    task_id=resolved_task_id,
                    step_id=resolved_step_id,
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
                    ErrorCode.APPROVAL_INVALID,
                    "The approval is invalid, expired, already used, or for a different action.",
                    task_id=resolved_task_id,
                    step_id=resolved_step_id,
                )

        known_errors: tuple[tuple[type[Exception], ErrorCode, str], ...] = (
            (
                FilesystemToolError,
                ErrorCode.INTERNAL_ERROR,
                "The filesystem operation failed or was rejected.",
            ),
            (
                TerminalToolError,
                ErrorCode.INVALID_ARGUMENTS,
                "The terminal operation was rejected or failed.",
            ),
            (
                GitToolError,
                ErrorCode.INTERNAL_ERROR,
                "The Git operation failed.",
            ),
            (
                BrowserToolError,
                ErrorCode.PERMISSION_DENIED,
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
                        task_id=resolved_task_id,
                        step_id=resolved_step_id,
                    )

            return self._failure(
                normalized_name,
                ErrorCode.INTERNAL_ERROR,
                "The tool failed during execution.",
                task_id=resolved_task_id,
                step_id=resolved_step_id,
            )

        return ToolResult(
            task_id=resolved_task_id,
            step_id=resolved_step_id,
            tool_name=normalized_name,
            status=ExecutionStatus.SUCCEEDED,
            success=True,
            output=output,
            verified=False,
        )
