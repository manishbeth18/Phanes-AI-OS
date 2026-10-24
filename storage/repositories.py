"""Public repository APIs for Phanes AI-OS persistence (Day 3 prototype).

Other modules use ONLY the classes in this file (via ``storage.Storage``);
they never open SQLite connections or write SQL (spec section 8).

Rules every method follows:

* Parameterized SQL only. Only generated text is the ``?`` placeholder list in
  ``TaskRepository.list_by_status``; no caller-supplied text is ever formatted
  into SQL.
* Each public method is one transaction. Returned models are read back from
  the database inside that transaction, so a returned value reflects what was
  actually saved. If the commit fails an exception is raised instead.
* Failures are explicit: ``RecordNotFoundError`` for missing records,
  ``ConstraintViolationError`` for rule/constraint violations and
  ``StorageError`` for other persistence failures.
* Timestamps must be timezone-aware; they are stored as UTC.

PROVISIONAL (not specified by the architecture; needs team agreement):
method parameters and return types beyond the method names listed in spec
section 6, the extra methods (``get_by_path``, ``list``, ``list_by_status``,
``create_many``, ``get``, ``list_for_task`` on steps, ``complete``), the
``Storage`` entry point, and the ``exit_code`` keyword of
``ExecutionRepository.complete`` (``ToolResult`` has no exit code field).
"""

from __future__ import annotations

import builtins
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from shared.enums import StepStatus, TaskStatus
from shared.exceptions import ConstraintViolationError, RecordNotFoundError
from shared.schemas import (
    ExecutionRecord,
    PlanStep,
    TaskRecord,
    ToolResult,
    Workspace,
    utc_now,
)
from storage.database import Database
from storage.models import (
    dumps_json,
    enum_value,
    execution_from_row,
    step_from_row,
    task_from_row,
    to_db_timestamp,
    workspace_from_row,
)

# Statuses a tool-execution attempt may have, and the subset that means the
# attempt has ended. (Execution status reuses StepStatus; PROVISIONAL.)
_ATTEMPT_STATUSES = frozenset(
    {
        StepStatus.RUNNING,
        StepStatus.COMPLETED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
        StepStatus.UNKNOWN,
    }
)
_FINAL_ATTEMPT_STATUSES = _ATTEMPT_STATUSES - {StepStatus.RUNNING}


# ---------------------------------------------------------------------------
# Row lookups shared by the repositories (all parameterized)
# ---------------------------------------------------------------------------


def _require_workspace(conn: sqlite3.Connection, workspace_id: str) -> Workspace:
    row = conn.execute(
        "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError(f"workspace not found: {workspace_id}")
    return workspace_from_row(row)


def _require_task(conn: sqlite3.Connection, task_id: str) -> TaskRecord:
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise RecordNotFoundError(f"task not found: {task_id}")
    return task_from_row(row)


def _require_step(conn: sqlite3.Connection, step_id: str) -> PlanStep:
    row = conn.execute(
        "SELECT * FROM plan_steps WHERE step_id = ?", (step_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError(f"plan step not found: {step_id}")
    return step_from_row(row)


def _fetch_execution_row(
    conn: sqlite3.Connection, execution_id: str
) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM tool_executions WHERE execution_id = ?", (execution_id,)
    ).fetchone()
    return row


def _require_execution(conn: sqlite3.Connection, execution_id: str) -> ExecutionRecord:
    row = _fetch_execution_row(conn, execution_id)
    if row is None:
        raise RecordNotFoundError(f"execution not found: {execution_id}")
    return execution_from_row(row)


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


class WorkspaceRepository:
    """Known project roots."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, workspace: Workspace) -> Workspace:
        """Insert the workspace, or update an existing one with the same id.

        An update changes ``root_path``, ``name`` and ``last_opened_at`` and
        preserves the stored ``created_at``. A different workspace already
        using the same ``root_path`` raises ``ConstraintViolationError``.
        """
        params = (
            workspace.workspace_id,
            str(workspace.root_path),
            workspace.name,
            to_db_timestamp(workspace.created_at),
            to_db_timestamp(workspace.last_opened_at),
        )
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO workspaces "
                "(workspace_id, root_path, name, created_at, last_opened_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET "
                "root_path = excluded.root_path, "
                "name = excluded.name, "
                "last_opened_at = excluded.last_opened_at",
                params,
            )
            return _require_workspace(conn, workspace.workspace_id)

    def get(self, workspace_id: str) -> Workspace:
        """Return the workspace or raise ``RecordNotFoundError``."""
        with self._db.transaction(write=False) as conn:
            return _require_workspace(conn, workspace_id)

    def get_by_path(self, root_path: Path) -> Workspace | None:
        """Return the workspace with exactly this root path, or ``None``."""
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE root_path = ?", (str(root_path),)
            ).fetchone()
            return None if row is None else workspace_from_row(row)

    def list(self) -> builtins.list[Workspace]:
        """Return all workspaces, oldest first."""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY created_at, workspace_id"
            ).fetchall()
            return [workspace_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskRepository:
    """User goals and their lifecycle status."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, task: TaskRecord) -> TaskRecord:
        """Persist a new task. Its workspace must already exist."""
        params = (
            task.task_id,
            task.workspace_id,
            task.goal,
            enum_value(TaskStatus, task.status),
            to_db_timestamp(task.created_at),
            to_db_timestamp(task.updated_at),
        )
        with self._db.transaction() as conn:
            _require_workspace(conn, task.workspace_id)
            conn.execute(
                "INSERT INTO tasks "
                "(task_id, workspace_id, goal, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
            )
            return _require_task(conn, task.task_id)

    def get(self, task_id: str) -> TaskRecord:
        """Return the task or raise ``RecordNotFoundError``."""
        with self._db.transaction(write=False) as conn:
            return _require_task(conn, task_id)

    def update_status(self, task_id: str, status: TaskStatus) -> TaskRecord:
        """Set the task status and ``updated_at`` (now, UTC).

        PROVISIONAL: status transitions are not validated here; the
        orchestrator owns workflow rules.
        """
        params = (enum_value(TaskStatus, status), to_db_timestamp(utc_now()), task_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"task not found: {task_id}")
            return _require_task(conn, task_id)

    def list_by_status(self, statuses: Sequence[TaskStatus]) -> list[TaskRecord]:
        """Return tasks whose status is in ``statuses``, oldest first."""
        values = [enum_value(TaskStatus, status) for status in statuses]
        if not values:
            return []
        placeholders = ", ".join("?" for _ in values)  # generated "?" only
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                "ORDER BY created_at, task_id",
                values,
            ).fetchall()
            return [task_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Plan steps
# ---------------------------------------------------------------------------


def _insert_step(
    conn: sqlite3.Connection, task_id: str, step: PlanStep, position: int
) -> None:
    conn.execute(
        "INSERT INTO plan_steps "
        "(step_id, task_id, position, tool, arguments_json, status, "
        "depends_on_json, requires_approval) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            step.step_id,
            task_id,
            position,
            step.tool,
            dumps_json(step.arguments),
            enum_value(StepStatus, step.status),
            dumps_json(step.depends_on),
            1 if step.requires_approval else 0,
        ),
    )


class StepRepository:
    """Plan steps: arguments, dependencies and state."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, task_id: str, step: PlanStep, position: int) -> PlanStep:
        """Persist one step at ``position``. The task must already exist."""
        with self._db.transaction() as conn:
            _require_task(conn, task_id)
            _insert_step(conn, task_id, step, position)
            return _require_step(conn, step.step_id)

    def create_many(self, task_id: str, steps: Sequence[PlanStep]) -> list[PlanStep]:
        """Atomically persist ``steps`` after any existing steps of the task.

        Either every step is saved or none is.
        """
        with self._db.transaction() as conn:
            _require_task(conn, task_id)
            start = conn.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM plan_steps "
                "WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            for offset, step in enumerate(steps):
                _insert_step(conn, task_id, step, start + offset)
            return [_require_step(conn, step.step_id) for step in steps]

    def update_status(
        self, task_id: str, step_id: str, status: StepStatus
    ) -> PlanStep:
        """Set a step's status. The step must belong to ``task_id``."""
        params = (enum_value(StepStatus, status), task_id, step_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE plan_steps SET status = ? WHERE task_id = ? AND step_id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(
                    f"plan step not found: {step_id} (task {task_id})"
                )
            return _require_step(conn, step_id)

    def get(self, step_id: str) -> PlanStep:
        """Return the step or raise ``RecordNotFoundError``."""
        with self._db.transaction(write=False) as conn:
            return _require_step(conn, step_id)

    def list_for_task(self, task_id: str) -> list[PlanStep]:
        """Return the task's steps in plan order; the task must exist."""
        with self._db.transaction(write=False) as conn:
            _require_task(conn, task_id)
            rows = conn.execute(
                "SELECT * FROM plan_steps WHERE task_id = ? ORDER BY position",
                (task_id,),
            ).fetchall()
            return [step_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Tool executions
# ---------------------------------------------------------------------------


class ExecutionRepository:
    """Individual execution attempts and their verified outcomes.

    Intended call order (PROVISIONAL, to agree with the executor owner):
    ``create`` a RUNNING record *before* the tool runs, then ``complete`` it
    from the ``ToolResult``. A record still RUNNING after a restart means the
    outcome is unknown and must be reconciled, never assumed.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, record: ExecutionRecord) -> ExecutionRecord:
        """Persist a new attempt in the RUNNING state, before the tool runs.

        The record must carry no outcome (no success, verification, output,
        exit code, error or finish time). The step must exist and belong to
        ``record.task_id``.
        """
        if (
            record.status is not StepStatus.RUNNING
            or record.finished_at is not None
            or record.success is not None
            or record.verified
            or record.verification_details is not None
            or record.error is not None
            or record.exit_code is not None
            or record.output != ""
        ):
            raise ConstraintViolationError(
                "an execution must be created in the 'running' state with no "
                "outcome fields set, before the tool runs"
            )
        params = (
            record.execution_id,
            record.task_id,
            record.step_id,
            record.attempt_number,
            enum_value(StepStatus, record.status),
            to_db_timestamp(record.started_at),
        )
        with self._db.transaction() as conn:
            belongs = conn.execute(
                "SELECT 1 FROM plan_steps WHERE task_id = ? AND step_id = ?",
                (record.task_id, record.step_id),
            ).fetchone()
            if belongs is None:
                raise RecordNotFoundError(
                    f"plan step not found: {record.step_id} (task {record.task_id})"
                )
            conn.execute(
                "INSERT INTO tool_executions "
                "(execution_id, task_id, step_id, attempt_number, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
            )
            return _require_execution(conn, record.execution_id)

    def complete(
        self,
        execution_id: str,
        result: ToolResult,
        *,
        exit_code: int | None = None,
    ) -> ExecutionRecord:
        """Record the outcome of a RUNNING attempt exactly as reported.

        Storage never upgrades or infers anything: ``success`` and
        ``verified`` are stored as given in ``result``. ``verified=True`` is
        only accepted together with ``verification_details`` describing the
        check, because storage cannot itself perform the check. An attempt
        can be completed only once.
        """
        if result.status not in _FINAL_ATTEMPT_STATUSES:
            raise ConstraintViolationError(
                f"cannot complete an execution with status {result.status!r}; "
                "expected completed, failed, cancelled or unknown"
            )
        details = result.verification_details
        if result.verified and not (details and details.strip()):
            raise ConstraintViolationError(
                "verified=True requires verification_details describing the "
                "check that was actually performed"
            )
        error_code = None if result.error is None else result.error.code
        error_json = (
            None
            if result.error is None
            else dumps_json(result.error.model_dump(mode="json"))
        )
        params = (
            enum_value(StepStatus, result.status),
            to_db_timestamp(utc_now()),
            1 if result.success else 0,
            1 if result.verified else 0,
            details,
            result.output,
            exit_code,
            error_code,
            error_json,
            execution_id,
        )
        with self._db.transaction() as conn:
            current = _fetch_execution_row(conn, execution_id)
            if current is None:
                raise RecordNotFoundError(f"execution not found: {execution_id}")
            if (current["task_id"], current["step_id"]) != (
                result.task_id,
                result.step_id,
            ):
                raise ConstraintViolationError(
                    "the result's task_id/step_id do not match the execution"
                )
            if current["status"] != StepStatus.RUNNING.value:
                raise ConstraintViolationError(
                    f"execution {execution_id} is already finalized "
                    f"({current['status']})"
                )
            conn.execute(
                "UPDATE tool_executions SET status = ?, finished_at = ?, "
                "success = ?, verified = ?, verification_details = ?, "
                "output = ?, exit_code = ?, error_code = ?, error_json = ? "
                "WHERE execution_id = ? AND status = 'running'",
                params,
            )
            return _require_execution(conn, execution_id)

    def get(self, execution_id: str) -> ExecutionRecord:
        """Return the execution or raise ``RecordNotFoundError``."""
        with self._db.transaction(write=False) as conn:
            return _require_execution(conn, execution_id)

    def list_for_task(self, task_id: str) -> list[ExecutionRecord]:
        """Return the task's attempts in start order; the task must exist."""
        with self._db.transaction(write=False) as conn:
            _require_task(conn, task_id)
            rows = conn.execute(
                "SELECT * FROM tool_executions WHERE task_id = ? "
                "ORDER BY started_at, attempt_number",
                (task_id,),
            ).fetchall()
            return [execution_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class Storage:
    """Opens the database, applies migrations and exposes the repositories.

    PROVISIONAL: the specification does not define how consumers obtain the
    repositories. This is the only way other modules should create them, which
    keeps ``Database`` and the SQLite connection private to ``storage/``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self.workspaces = WorkspaceRepository(db)
        self.tasks = TaskRepository(db)
        self.steps = StepRepository(db)
        self.executions = ExecutionRepository(db)

    @classmethod
    def open(cls, db_path: Path) -> "Storage":
        """Open (creating if needed) the database at ``db_path`` and migrate it."""
        db = Database(db_path)
        db.open()
        try:
            db.migrate()
        except BaseException:
            db.close()
            raise
        return cls(db)

    def schema_version(self) -> int:
        """Return the applied schema version."""
        return self._db.schema_version()

    def close(self) -> None:
        """Close the underlying database. Safe to call more than once."""
        self._db.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "ExecutionRepository",
    "StepRepository",
    "Storage",
    "TaskRepository",
    "WorkspaceRepository",
]
