"""Storage-internal helpers: value conversion and row-to-model mapping.

This module deliberately defines NO public data contracts. Shared models
(``Workspace``, ``TaskRecord``, ``PlanStep``, ``ExecutionRecord``, ...) live in
``shared/schemas.py``; the functions here only convert between those models
and SQLite rows. Nothing outside ``storage/`` should import this module.

PROVISIONAL: the specification lists ``storage/models.py`` without describing
its contents; this is the role proposed in the design response.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from shared.enums import StepStatus, TaskStatus
from shared.exceptions import ConstraintViolationError, StorageError
from shared.schemas import (
    ExecutionRecord,
    PlanStep,
    TaskRecord,
    ToolError,
    Workspace,
)

_E = TypeVar("_E", bound=Enum)


# ---- value conversion ----------------------------------------------------


def to_db_timestamp(value: datetime) -> str:
    """Serialize a timezone-aware datetime as an ISO-8601 UTC string."""
    if not isinstance(value, datetime):
        raise ConstraintViolationError(
            f"expected a datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConstraintViolationError(
            "persisted timestamps must be timezone-aware (UTC); got a naive datetime"
        )
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def from_db_timestamp(value: str) -> datetime:
    """Parse a stored timestamp back into a timezone-aware UTC datetime."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"stored timestamp is invalid: {value!r}") from exc
    if parsed.tzinfo is None:
        raise StorageError(f"stored timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def enum_value(enum_type: type[_E], value: Any) -> str:
    """Return the string value of ``value`` if it is a valid ``enum_type``."""
    try:
        return str(enum_type(value).value)
    except ValueError as exc:
        raise ConstraintViolationError(
            f"{value!r} is not a valid {enum_type.__name__}"
        ) from exc


def dumps_json(value: Any) -> str:
    """Serialize ``value`` to JSON text, or raise ConstraintViolationError."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ConstraintViolationError(
            f"value is not JSON-serializable: {exc}"
        ) from exc


def loads_json(text: str) -> Any:
    """Parse stored JSON text, or raise StorageError if it is corrupt."""
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"stored JSON is corrupt: {exc}") from exc


# ---- row -> shared model -------------------------------------------------


def workspace_from_row(row: sqlite3.Row) -> Workspace:
    try:
        return Workspace(
            workspace_id=row["workspace_id"],
            root_path=Path(row["root_path"]),
            name=row["name"],
            created_at=from_db_timestamp(row["created_at"]),
            last_opened_at=from_db_timestamp(row["last_opened_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"stored workspace row is invalid: {exc}") from exc


def task_from_row(row: sqlite3.Row) -> TaskRecord:
    try:
        return TaskRecord(
            task_id=row["task_id"],
            workspace_id=row["workspace_id"],
            goal=row["goal"],
            status=TaskStatus(row["status"]),
            created_at=from_db_timestamp(row["created_at"]),
            updated_at=from_db_timestamp(row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"stored task row is invalid: {exc}") from exc


def step_from_row(row: sqlite3.Row) -> PlanStep:
    try:
        return PlanStep(
            step_id=row["step_id"],
            tool=row["tool"],
            arguments=loads_json(row["arguments_json"]),
            status=StepStatus(row["status"]),
            depends_on=loads_json(row["depends_on_json"]),
            requires_approval=bool(row["requires_approval"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"stored plan step row is invalid: {exc}") from exc


def execution_from_row(row: sqlite3.Row) -> ExecutionRecord:
    try:
        error = None
        if row["error_json"] is not None:
            error = ToolError.model_validate(loads_json(row["error_json"]))
        finished_at = row["finished_at"]
        success = row["success"]
        return ExecutionRecord(
            execution_id=row["execution_id"],
            task_id=row["task_id"],
            step_id=row["step_id"],
            attempt_number=row["attempt_number"],
            status=StepStatus(row["status"]),
            started_at=from_db_timestamp(row["started_at"]),
            finished_at=None if finished_at is None else from_db_timestamp(finished_at),
            success=None if success is None else bool(success),
            verified=bool(row["verified"]),
            verification_details=row["verification_details"],
            output=row["output"],
            exit_code=row["exit_code"],
            error=error,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"stored execution row is invalid: {exc}") from exc
