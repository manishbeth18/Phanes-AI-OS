
"""Canonical shared contracts for PHANES.

All modules must import shared types from this file.
Do not create duplicate versions of these models in feature packages.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


class ErrorCode(str, Enum):
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_BLOCKED = "TOOL_BLOCKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    WORKSPACE_NOT_FOUND = "WORKSPACE_NOT_FOUND"
    DATABASE_ERROR = "DATABASE_ERROR"
    TEST_FAILURE = "TEST_FAILURE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class ToolError:
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    requires_approval: bool = False
    description: str = ""


@dataclass(frozen=True)
class Plan:
    task_id: str
    steps: list[PlanStep]
    rationale: str = ""


@dataclass(frozen=True)
class ToolResult:
    task_id: str
    step_id: str
    status: ExecutionStatus
    success: bool
    output: Any = None
    error: ToolError | None = None
    verified: bool = False
    verification_details: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    task_id: str
    step_id: str
    tool: str
    arguments: dict[str, Any]
    summary: str
    expires_at: datetime


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    task_id: str
    step_id: str
    approved: bool
    decided_at: datetime
