
import subprocess
import time

import pytest

from shared.contracts import Plan, PlanStep, TaskStatus as ContractTaskStatus
from shared.enums import StepStatus, TaskStatus
from shared.task_service_impl import ConcreteTaskService
from storage.task_persistence_adapter import TaskPersistenceAdapter


TERMINAL_STATUSES = {
    ContractTaskStatus.SUCCEEDED,
    ContractTaskStatus.FAILED,
    ContractTaskStatus.CANCELLED,
    ContractTaskStatus.NEEDS_ATTENTION,
}


def wait_for_terminal_status(service, task_id, timeout=10):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        status = service.get_task_status(task_id)
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(0.02)

    pytest.fail(f"Task {task_id} did not finish within {timeout} seconds.")


def initialize_git_repository(workspace):
    subprocess.run(
        ["git", "init"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def test_task_service_persists_successful_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "example.txt").write_text("test", encoding="utf-8")
    initialize_git_repository(workspace)

    persistence = TaskPersistenceAdapter(tmp_path / "tasks.sqlite3")
    events = []

    def planner(goal, workspace_path, context):
        return Plan(
            task_id="test-plan",
            steps=[
                PlanStep(
                    step_id="inspect",
                    tool="filesystem.list_directory",
                    arguments={"path": "."},
                )
            ],
        )

    service = ConcreteTaskService(
        planner=planner,
        repository_root=str(workspace),
        persistence=persistence,
    )
    service.subscribe(events.append)

    try:
        task_id = service.submit("List workspace files", str(workspace))
        status = wait_for_terminal_status(service, task_id)

        stored_task = persistence.storage.tasks.get(task_id)
        stored_steps = persistence.storage.steps.list_for_task(task_id)
        executions = persistence.storage.executions.list_for_task(task_id)

        log_messages = [
            event.message
            for event in events
            if hasattr(event, "message")
        ]

        assert status is ContractTaskStatus.SUCCEEDED, (
            f"Task status: {status!r}\n"
            f"Logs: {log_messages!r}\n"
            f"Stored task: {stored_task!r}\n"
            f"Stored steps: {stored_steps!r}\n"
            f"Executions: {executions!r}"
        )

        assert stored_task is not None
        assert stored_task.status is TaskStatus.COMPLETED
        assert len(stored_steps) == 1
        assert stored_steps[0].step_id == "inspect"
        assert stored_steps[0].status is StepStatus.COMPLETED
        assert len(executions) == 1
        assert executions[0].status is StepStatus.COMPLETED
        assert executions[0].finished_at is not None

    finally:
        persistence.close()


def test_task_service_persists_planning_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    persistence = TaskPersistenceAdapter(tmp_path / "tasks.sqlite3")

    def planner(goal, workspace_path, context):
        raise RuntimeError("Intentional test planning failure")

    service = ConcreteTaskService(
        planner=planner,
        persistence=persistence,
    )

    try:
        task_id = service.submit("Test planning failure", str(workspace))
        status = wait_for_terminal_status(service, task_id)

        assert status is ContractTaskStatus.FAILED

        stored_task = persistence.storage.tasks.get(task_id)
        assert stored_task is not None
        assert stored_task.status is TaskStatus.FAILED

    finally:
        persistence.close()
