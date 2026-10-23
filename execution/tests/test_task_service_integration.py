
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


def test_task_service_persists_execution_when_cancelled_during_tool(tmp_path):
    """A completed tool must not remain RUNNING when its task is cancelled."""
    import threading
    from unittest.mock import patch

    from execution.executor import Executor
    from execution.tool_registry import ToolRegistry, ToolSafetyLevel

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "example.txt").write_text("test", encoding="utf-8")
    initialize_git_repository(workspace)

    persistence = TaskPersistenceAdapter(tmp_path / "tasks.sqlite3")
    tool_started = threading.Event()
    allow_tool_to_finish = threading.Event()

    def blocking_tool():
        tool_started.set()
        allow_tool_to_finish.wait(timeout=5)
        return "tool finished"

    def planner(goal, workspace_path, context):
        return Plan(
            task_id="cancel-plan",
            steps=[
                PlanStep(
                    step_id="blocking",
                    tool="test.blocking",
                    arguments={},
                )
            ],
        )

    registry = ToolRegistry()
    registry.register(
        "test.blocking",
        blocking_tool,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    executor = Executor(registry)

    class FakeGate:
        def __init__(self, approval_manager):
            self.approval_manager = approval_manager

    try:
        with patch(
            "shared.task_service_impl.create_execution_engine",
            return_value=(None, executor),
        ), patch(
            "shared.task_service_impl.ApprovalGate",
            FakeGate,
        ):
            service = ConcreteTaskService(
                planner=planner,
                repository_root=str(workspace),
                persistence=persistence,
            )
            task_id = service.submit("Run blocking test tool", str(workspace))

            assert tool_started.wait(timeout=5), (
                f"Tool did not start. Task status: {service.get_task_status(task_id)!r}"
            )
            service.cancel(task_id)
            allow_tool_to_finish.set()

            status = wait_for_terminal_status(service, task_id)

        stored_task = persistence.storage.tasks.get(task_id)
        executions = persistence.storage.executions.list_for_task(task_id)

        assert status is ContractTaskStatus.CANCELLED
        assert stored_task is not None
        assert stored_task.status is TaskStatus.CANCELLED
        assert len(executions) == 1
        assert executions[0].status is StepStatus.COMPLETED
        assert executions[0].finished_at is not None
        assert executions[0].success is True
    finally:
        allow_tool_to_finish.set()
        persistence.close()


