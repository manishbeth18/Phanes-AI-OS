"""Canonical shared data contracts for Phanes AI-OS.

Models marked "SPEC STARTER" are copied from the illustrative starter schemas
in the architecture specification (section 5). Even those are *not frozen*
until the team reviews them at the contract freeze.

Models marked "PROVISIONAL" are named in the specification
(``Workspace``, ``TaskRecord``, ``ExecutionRecord``) but their fields are not
defined there. The fields below are the minimum needed by the Day 3 SQLite
prototype. Other developers MUST review them before depending on them; any
change after the freeze follows the contract change process (spec section 19).

Persisted timestamps must be timezone-aware UTC. The storage layer rejects
naive datetimes.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# Re-exported so that ``from shared.schemas import TaskStatus`` (the import
# style implied by the specification's starter code) keeps working.
from shared.enums import StepStatus, TaskStatus

__all__ = [
    "ExecutionRecord",
    "PlanStep",
    "StepStatus",
    "TaskPlan",
    "TaskRecord",
    "TaskStatus",
    "ToolError",
    "ToolResult",
    "Workspace",
    "new_id",
    "utc_now",
]


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Return a new unique identifier (UUID4 string).

    PROVISIONAL: the specification allows "UUIDs or another team-approved ID
    strategy". Callers generate identifiers; repositories never invent them.
    """
    return str(uuid4())


# --------------------------------------------------------------------------
# SPEC STARTER models (copied from the specification, section 5)
# --------------------------------------------------------------------------


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str
    workspace_id: str
    status: TaskStatus = TaskStatus.PENDING
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    step_id: str
    status: StepStatus
    success: bool
    output: str = ""
    error: ToolError | None = None
    verified: bool = False
    verification_details: str | None = None


# --------------------------------------------------------------------------
# PROVISIONAL models (named by the specification, fields proposed here)
# --------------------------------------------------------------------------


class Workspace(BaseModel):
    """A known project root. PROVISIONAL.

    ``root_path`` should be the resolved absolute path produced by workspace
    discovery; storage compares paths as exact strings.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    root_path: Path
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    last_opened_at: datetime = Field(default_factory=utc_now)


class TaskRecord(BaseModel):
    """A persisted user goal and its lifecycle state. PROVISIONAL.

    The plan's steps are persisted separately (see ``PlanStep``).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    workspace_id: str
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionRecord(BaseModel):
    """One execution attempt of one plan step. PROVISIONAL.

    Lifecycle: created with ``status=RUNNING`` and no outcome fields *before*
    the tool runs; later finalized from a ``ToolResult``. ``success`` is
    ``None`` until an outcome is recorded, so an unfinished or interrupted
    attempt is never presented as successful.

    ``verified`` has the specification's meaning: the stated outcome was
    actually checked. It does not mean the attempt succeeded.
    """

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    task_id: str
    step_id: str
    attempt_number: int
    status: StepStatus = StepStatus.RUNNING
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    success: bool | None = None
    verified: bool = False
    verification_details: str | None = None
    output: str = ""
    exit_code: int | None = None
    error: ToolError | None = None
