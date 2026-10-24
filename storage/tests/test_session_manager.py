
"""Tests for recovering persisted sessions."""

from shared.enums import StepStatus, TaskStatus
from shared.schemas import ToolResult
from storage.session_manager import SessionManager
from storage.tests.factories import make_running_execution


def test_recover_restores_task_plan_and_execution_history(storage, seeded):
    execution = make_running_execution(
        seeded.task.task_id,
        seeded.step.step_id,
    )
    storage.executions.create(execution)

    manager = SessionManager(storage)
    recovered = manager.recover(seeded.task.task_id)

    assert recovered.task.task_id == seeded.task.task_id
    assert recovered.steps[0].step_id == seeded.step.step_id
    assert len(recovered.executions) == 1
    assert recovered.executions[0].execution_id == execution.execution_id


def test_recover_identifies_interrupted_execution_without_rerunning(
    storage, seeded
):
    execution = make_running_execution(
        seeded.task.task_id,
        seeded.step.step_id,
    )
    storage.executions.create(execution)

    manager = SessionManager(storage)
    recovered = manager.recover(seeded.task.task_id)

    assert len(recovered.interrupted_executions) == 1
    assert (
        recovered.interrupted_executions[0].status
        == StepStatus.RUNNING
    )

    # Recovery must not silently change the stored execution outcome.
    stored = storage.executions.get(execution.execution_id)
    assert stored.status == StepStatus.RUNNING
    assert stored.success is None


def test_find_recoverable_tasks_excludes_completed_tasks(storage, seeded):
    storage.tasks.update_status(seeded.task.task_id, TaskStatus.RUNNING)

    recoverable = SessionManager(storage).find_recoverable_tasks()

    assert any(task.task_id == seeded.task.task_id for task in recoverable)

    storage.tasks.update_status(seeded.task.task_id, TaskStatus.COMPLETED)

    recoverable = SessionManager(storage).find_recoverable_tasks()

    assert all(task.task_id != seeded.task.task_id for task in recoverable)
