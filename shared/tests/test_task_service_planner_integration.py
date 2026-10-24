import subprocess

import pytest

from planner.planner import Planner
from shared.context import ConversationMessage, TaskContext
from shared.contracts import Plan, PlanStep, TaskStatus
from shared.task_service_impl import ConcreteTaskService


def initialize_git_repo(path):
    commands = [
        ["git", "init"],
        ["git", "config", "user.name", "PHANES Tests"],
        ["git", "config", "user.email", "phanes-tests@example.com"],
    ]
    for command in commands:
        subprocess.run(
            command, cwd=path, check=True, capture_output=True, text=True
        )

    (path / "README.md").write_text(
        "# PHANES test workspace\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial test commit"],
        cwd=path, check=True, capture_output=True, text=True
    )


def wait_for_terminal(service, task_id, timeout=5):
    record = service._tasks[task_id]
    assert record.thread is not None
    record.thread.join(timeout)
    assert not record.thread.is_alive(), "Task worker did not finish in time"
    return service.get_task_status(task_id), record.message


def test_p1_planner_receives_canonical_context_and_task_id(tmp_path):
    initialize_git_repo(tmp_path)
    captured = {}

    def provider(*, goal, workspace_id, context, task_id):
        captured.update(
            goal=goal, workspace_id=workspace_id,
            context=context, task_id=task_id,
        )
        return Plan(task_id=task_id, steps=[])

    service = ConcreteTaskService(Planner(provider))
    task_id = service.submit("  inspect project  ", str(tmp_path))
    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.SUCCEEDED, message
    assert captured["goal"] == "inspect project"
    assert captured["task_id"] == task_id
    assert isinstance(captured["context"], TaskContext)
    assert captured["context"].workspace_id == captured["workspace_id"]
    assert captured["context"].workspace_path == str(tmp_path.resolve())
    assert captured["context"].conversation_history == [
        ConversationMessage(role="user", content="inspect project")
    ]


def test_p1_planner_preserves_stable_workspace_id(tmp_path):
    initialize_git_repo(tmp_path)
    captured = {}
    context = TaskContext(
        workspace_id="workspace-123",
        workspace_path=str(tmp_path.resolve()),
    )

    def provider(*, goal, workspace_id, context, task_id):
        captured.update(workspace_id=workspace_id, context=context)
        return Plan(task_id=task_id, steps=[])

    service = ConcreteTaskService(
        Planner(provider),
        context_provider=lambda workspace, goal: context,
    )
    task_id = service.submit("inspect project", str(tmp_path))
    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.SUCCEEDED, message
    assert captured["workspace_id"] == "workspace-123"
    assert captured["context"] is context


def test_p1_planner_rejects_context_for_another_workspace(tmp_path):
    initialize_git_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    context = TaskContext(
        workspace_id="other-workspace",
        workspace_path=str(other.resolve()),
    )
    called = []

    def provider(**kwargs):
        called.append(True)
        return Plan(task_id=kwargs["task_id"], steps=[])

    service = ConcreteTaskService(
        Planner(provider),
        context_provider=lambda workspace, goal: context,
    )
    task_id = service.submit("inspect project", str(tmp_path))
    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.FAILED
    assert "Context workspace path does not match" in message
    assert called == []


def test_p1_planner_rejects_unregistered_tool(tmp_path):
    initialize_git_repo(tmp_path)

    def provider(**kwargs):
        return Plan(
            task_id=kwargs["task_id"],
            steps=[PlanStep("s1", "not.a.registered.tool")],
        )

    service = ConcreteTaskService(Planner(provider))
    task_id = service.submit("do something", str(tmp_path))
    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.FAILED
    assert "unknown tool" in message.lower()


def test_legacy_callable_planner_remains_supported(tmp_path):
    initialize_git_repo(tmp_path)
    captured = {}

    def legacy_planner(goal, workspace, context):
        captured.update(goal=goal, workspace=workspace, context=context)
        return Plan(task_id="legacy-plan", steps=[])

    service = ConcreteTaskService(legacy_planner)
    task_id = service.submit("inspect project", str(tmp_path))
    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.SUCCEEDED, message
    assert captured["goal"] == "inspect project"
    assert captured["workspace"] == str(tmp_path)
    assert captured["context"] is None
def test_terminal_command_requires_approval_and_rejection_prevents_execution(tmp_path):
    import sys
    import threading

    from shared.contracts import ApprovalDecision
    from shared.service import ApprovalRequestedEvent, StepFinishedEvent

    initialize_git_repo(tmp_path)

    events = []
    approval_requested = threading.Event()
    step_finished = threading.Event()

    def provider(**kwargs):
        return Plan(
            task_id=kwargs["task_id"],
            steps=[
                PlanStep(
                    step_id="terminal-1",
                    tool="terminal.run_command",
                    arguments={
                        "command": [
                            sys.executable,
                            "-c",
                            "raise SystemExit('COMMAND_SHOULD_NOT_RUN')",
                        ]
                    },
                )
            ],
        )

    service = ConcreteTaskService(
        Planner(provider),
        approval_timeout_seconds=10,
    )

    def on_event(event):
        events.append(event)
        if isinstance(event, ApprovalRequestedEvent):
            approval_requested.set()
        elif isinstance(event, StepFinishedEvent):
            step_finished.set()

    service.subscribe(on_event)
    task_id = service.submit("Run a command", str(tmp_path))

    assert approval_requested.wait(5), "No approval request was emitted"

    request_event = next(
        event for event in events
        if isinstance(event, ApprovalRequestedEvent)
    )
    request = request_event.request

    service.resolve_approval(
        ApprovalDecision(
            approval_id=request.approval_id,
            task_id=request.task_id,
            step_id=request.step_id,
            approved=False,
            decided_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
    )

    status, message = wait_for_terminal(service, task_id)

    assert status == TaskStatus.FAILED, message
    assert step_finished.wait(1), "Rejected step did not finish"

    result_event = next(
        event for event in events
        if isinstance(event, StepFinishedEvent)
    )
    assert result_event.result.success is False
    assert result_event.result.error is not None
    assert result_event.result.error.code.value == "APPROVAL_REJECTED"
    assert "COMMAND_SHOULD_NOT_RUN" not in str(result_event.result.output)
