
"""UI-facing PHANES service and event contracts."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from shared.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    TaskStatus,
    ToolResult,
)


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    status: TaskStatus
    message: str = ""


@dataclass(frozen=True)
class StepStartedEvent:
    task_id: str
    step_id: str
    tool: str


@dataclass(frozen=True)
class StepFinishedEvent:
    result: ToolResult


@dataclass(frozen=True)
class LogEvent:
    task_id: str
    level: str
    message: str
    step_id: str | None = None


@dataclass(frozen=True)
class ApprovalRequestedEvent:
    request: ApprovalRequest


@dataclass(frozen=True)
class ApprovalResolvedEvent:
    approval_id: str
    task_id: str
    step_id: str
    approved: bool
    message: str = ""


@dataclass(frozen=True)
class TaskFinishedEvent:
    task_id: str
    status: TaskStatus
    summary: str = ""


PHANES_EVENT_TYPES = (
    TaskEvent,
    StepStartedEvent,
    StepFinishedEvent,
    LogEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    TaskFinishedEvent,
)


class TaskService(Protocol):
    def subscribe(
        self,
        callback: Callable[[object], None],
    ) -> Callable[[], None]:
        """Subscribe to backend events; return an unsubscribe function."""
        ...

    def submit(self, goal: str, workspace: str) -> str:
        """Submit a task and return its task ID."""
        ...

    def cancel(self, task_id: str) -> None:
        """Request cancellation; this does not guarantee immediate stopping."""
        ...

    def resolve_approval(self, decision: ApprovalDecision) -> None:
        """Submit an approval decision for backend validation."""
        ...

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Return the persisted/current task status."""
        ...
