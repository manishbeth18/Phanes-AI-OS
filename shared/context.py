"""Canonical context models for PHANES.

Context-specific models belong here.
Core task, execution, and approval contracts belong in shared/contracts.py.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextFile:
    """A relevant file selected for task context."""

    path: str
    content: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ConversationMessage:
    """A conversation message relevant to the current task."""

    role: str
    content: str


@dataclass(frozen=True)
class PreviousTaskResult:
    """Relevant information from a previous task or execution."""

    task_id: str
    summary: str
    status: str | None = None
    output: str | None = None
    verified: bool = False
    verification_details: str | None = None


@dataclass(frozen=True)
class TaskContext:
    """Structured context supplied to the PHANES planner."""

    workspace_id: str
    workspace_path: str
    project_type: str | None = None
    relevant_files: list[ContextFile] = field(default_factory=list)
    previous_results: list[PreviousTaskResult] = field(
        default_factory=list
    )
    conversation_history: list[ConversationMessage] = field(
        default_factory=list
    )
    metadata: dict[str, Any] = field(default_factory=dict)