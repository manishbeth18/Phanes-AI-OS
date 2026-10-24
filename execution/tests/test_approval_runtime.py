from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from execution.approval import ApprovalManager
from execution.approval_gate import ApprovalGate, ApprovalGateError
from execution.approval_runtime import ApprovalRuntime
from shared.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ExecutionStatus,
    ToolError,
    ToolResult,
)


class FakeExecutor:
    def __init__(self, manager: ApprovalManager) -> None:
        self.approval_manager = manager
        self.execute = Mock(
            side_effect=lambda tool_name, arguments, **kwargs: ToolResult(
                task_id=kwargs["task_id"],
                step_id=kwargs["step_id"],
                tool_name=tool_name,
                status=ExecutionStatus.SUCCEEDED,
                success=True,
                output={"ran": True},
            )
        )


def make_request(approval_id: str = "approval-1", arguments=None) -> ApprovalRequest:
    now = datetime.now(timezone.utc)
    return ApprovalRequest(
        approval_id=approval_id,
        task_id="task-1",
        step_id="step-1",
        tool="filesystem.write_file",
        arguments=arguments if arguments is not None else {
            "path": "notes.txt",
            "content": "approved content",
        },
        summary="Write a file",
        expires_at=now + timedelta(minutes=2),
    )


def make_decision(request: ApprovalRequest, approved: bool = True) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id=request.step_id,
        approved=approved,
        decided_at=datetime.now(timezone.utc),
    )


def make_runtime():
    manager = ApprovalManager()
    executor = FakeExecutor(manager)
    gate = ApprovalGate(approval_manager=manager)
    # ApprovalRuntime deliberately validates the real executor type. Use a
    # small concrete Executor shell for these isolated coordination tests.
    from execution.executor import Executor
    from execution.tool_registry import ToolRegistry

    real_executor = Executor(ToolRegistry(), approval_manager=manager)
    real_executor.execute = executor.execute
    return ApprovalRuntime(real_executor, gate), executor.execute, manager


def test_approved_request_executes_exact_registered_action_once():
    runtime, execute, _ = make_runtime()
    request = make_request()
    runtime.register_request(request)

    outcome = runtime.resolve_and_execute(make_decision(request))

    assert outcome.resolution.approved is True
    assert outcome.resolution.token
    assert outcome.tool_result is not None
    assert outcome.tool_result.success is True
    execute.assert_called_once_with(
        "filesystem.write_file",
        {"path": "notes.txt", "content": "approved content"},
        requires_approval=True,
        approval_token=outcome.resolution.token,
        task_id="task-1",
        step_id="step-1",
    )


def test_rejected_request_does_not_execute():
    runtime, execute, _ = make_runtime()
    request = make_request()
    runtime.register_request(request)

    outcome = runtime.resolve_and_execute(make_decision(request, approved=False))

    assert outcome.resolution.approved is False
    assert outcome.resolution.token is None
    assert outcome.tool_result is None
    execute.assert_not_called()


def test_mismatched_decision_does_not_execute_or_consume_valid_request():
    runtime, execute, _ = make_runtime()
    request = make_request()
    runtime.register_request(request)
    wrong = ApprovalDecision(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id="wrong-step",
        approved=True,
        decided_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ApprovalGateError):
        runtime.resolve_and_execute(wrong)
    execute.assert_not_called()

    outcome = runtime.resolve_and_execute(make_decision(request))
    assert outcome.tool_result is not None
    execute.assert_called_once()


def test_request_argument_mutation_after_registration_cannot_change_action():
    runtime, execute, _ = make_runtime()
    request = make_request()
    runtime.register_request(request)
    request.arguments["content"] = "mutated after registration"

    outcome = runtime.resolve_and_execute(make_decision(request))

    assert outcome.tool_result is not None
    called_args = execute.call_args.args[1]
    assert called_args["content"] == "approved content"


def test_duplicate_request_is_rejected():
    runtime, execute, _ = make_runtime()
    request = make_request()
    runtime.register_request(request)

    with pytest.raises(ApprovalGateError):
        runtime.register_request(request)

    outcome = runtime.resolve_and_execute(make_decision(request))
    assert outcome.tool_result is not None
    execute.assert_called_once()


def test_runtime_requires_shared_approval_manager():
    from execution.executor import Executor
    from execution.tool_registry import ToolRegistry

    executor = Executor(ToolRegistry(), approval_manager=ApprovalManager())
    gate = ApprovalGate(approval_manager=ApprovalManager())

    with pytest.raises(ValueError, match="share the same ApprovalManager"):
        ApprovalRuntime(executor, gate)
