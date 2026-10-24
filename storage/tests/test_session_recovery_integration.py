
"""Integration tests for session recovery across multiple tasks."""

from shared.enums import TaskStatus
from storage.session_manager import SessionManager
from storage.tests.factories import make_task


def test_recovery_lists_multiple_unfinished_tasks_and_excludes_terminal_tasks(
    storage, seeded
):
    # Create two additional tasks in the same workspace.
    running_task = make_task(
        seeded.task.workspace_id,
        goal="unfinished running task",
    )
    pending_task = make_task(
        seeded.task.workspace_id,
        goal="unfinished pending task",
    )
    completed_task = make_task(
        seeded.task.workspace_id,
        goal="completed task",
    )
    failed_task = make_task(
        seeded.task.workspace_id,
        goal="failed task",
        status=TaskStatus.FAILED,
    )

    # Persist all tasks.
    storage.tasks.create(running_task)
    storage.tasks.create(pending_task)
    storage.tasks.create(completed_task)
    storage.tasks.create(failed_task)

    # Set their intended statuses.
    storage.tasks.update_status(
        running_task.task_id, TaskStatus.RUNNING
    )
    storage.tasks.update_status(
        pending_task.task_id, TaskStatus.PENDING
    )
    storage.tasks.update_status(
        completed_task.task_id, TaskStatus.COMPLETED
    )

    recoverable = SessionManager(storage).find_recoverable_tasks()
    recoverable_ids = {task.task_id for task in recoverable}

    # Both unfinished tasks should be recoverable.
    assert running_task.task_id in recoverable_ids
    assert pending_task.task_id in recoverable_ids

    # Terminal tasks should not be recoverable.
    assert completed_task.task_id not in recoverable_ids
    assert failed_task.task_id not in recoverable_ids
