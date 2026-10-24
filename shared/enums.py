"""Shared status enumerations for Phanes AI-OS.

TaskStatus and StepStatus are copied unchanged from the starter schemas in
the architecture specification (section 5). Always use these enums instead of
inventing status strings such as "done", "finished" or "success".

PROVISIONAL: the specification lists ``shared/enums.py`` in the suggested
layout but its starter code defines the enums inside ``shared/schemas.py``.
They live here and are re-exported from ``shared.schemas`` so that both import
styles work. The team must confirm the final location at the contract freeze.
"""

from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
