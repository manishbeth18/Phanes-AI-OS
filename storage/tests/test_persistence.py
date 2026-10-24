"""Persistence tests: data must survive closing and reopening the database file.

Closing and reopening ``Storage`` is only a stand-in for an application
restart. It does not exercise session recovery, which is not implemented yet.
"""

from __future__ import annotations

from pathlib import Path

from shared.enums import StepStatus, TaskStatus
from shared.schemas import ToolError
from storage.repositories import Storage
from storage.tests.factories import (
    make_result,
    make_running_execution,
    make_step,
    make_task,
    make_workspace,
)


def test_full_task_history_survives_close_and_reopen(db_path: Path, tmp_path: Path) -> None:
    first = Storage.open(db_path)
    workspace = first.workspaces.upsert(make_workspace(tmp_path / "project", name="project"))
    task = first.tasks.create(make_task(workspace.workspace_id, goal="run the tests"))
    steps = first.steps.create_many(
        task.task_id,
        [make_step(tool="fs.read", arguments={"path": "a.py"}), make_step(tool="terminal.run")],
    )
    first.tasks.update_status(task.task_id, TaskStatus.RUNNING)
    first.steps.update_status(task.task_id, steps[0].step_id, StepStatus.COMPLETED)

    finished = first.executions.create(
        make_running_execution(task.task_id, steps[0].step_id)
    )
    finished = first.executions.complete(
        finished.execution_id,
        make_result(
            task.task_id,
            steps[0].step_id,
            output="contents",
            verified=True,
            verification_details="file existed after the read",
        ),
        exit_code=0,
    )
    failed = first.executions.create(make_running_execution(task.task_id, steps[1].step_id))
    failed = first.executions.complete(
        failed.execution_id,
        make_result(
            task.task_id,
            steps[1].step_id,
            status=StepStatus.FAILED,
            success=False,
            error=ToolError(code="EXECUTION_TIMEOUT", message="timed out", retryable=True),
        ),
    )
    saved_task = first.tasks.get(task.task_id)
    saved_steps = first.steps.list_for_task(task.task_id)
    first.close()

    second = Storage.open(db_path)
    try:
        assert second.schema_version() == 1
        assert second.workspaces.get(workspace.workspace_id) == workspace
        assert second.workspaces.get_by_path(tmp_path / "project") == workspace
        assert second.tasks.get(task.task_id) == saved_task
        assert second.tasks.get(task.task_id).status is TaskStatus.RUNNING
        assert second.steps.list_for_task(task.task_id) == saved_steps
        assert second.steps.get(steps[0].step_id).status is StepStatus.COMPLETED
        assert second.executions.list_for_task(task.task_id) == [finished, failed]
        assert second.executions.get(failed.execution_id).error is not None
        assert second.executions.get(failed.execution_id).error.retryable is True
    finally:
        second.close()


def test_unfinished_execution_is_still_running_after_reopen(
    db_path: Path, tmp_path: Path
) -> None:
    """An attempt that never completed must not turn into a success."""
    first = Storage.open(db_path)
    workspace = first.workspaces.upsert(make_workspace(tmp_path / "project"))
    task = first.tasks.create(make_task(workspace.workspace_id))
    step = first.steps.create_many(task.task_id, [make_step()])[0]
    started = first.executions.create(make_running_execution(task.task_id, step.step_id))
    first.close()  # the tool "crashed" before complete() was called

    second = Storage.open(db_path)
    try:
        reloaded = second.executions.get(started.execution_id)
        assert reloaded.status is StepStatus.RUNNING
        assert reloaded.success is None
        assert reloaded.verified is False
        assert reloaded.finished_at is None
    finally:
        second.close()
