import pytest

from shared.context import TaskContext
from shared.contracts import ErrorCode, ExecutionStatus, Plan, PlanStep, ToolError, ToolResult
from planner.planner import PlannerError
from planner.replanner import ReplanAction, Replanner


def plan():
    return Plan("task-1", [PlanStep("step-1", "tool.read")])


def result(*, status=ExecutionStatus.FAILED, success=False, error=None, verified=False):
    return ToolResult("task-1", "step-1", status, success, error=error, verified=verified)


def test_verified_success_continues():
    decision = Replanner().evaluate_result(
        plan(), result(status=ExecutionStatus.SUCCEEDED, success=True, verified=True)
    )
    assert decision.action == ReplanAction.CONTINUE


def test_unverified_success_requests_replan_for_verification():
    decision = Replanner().evaluate_result(
        plan(), result(status=ExecutionStatus.SUCCEEDED, success=True, verified=False)
    )
    assert decision.action == ReplanAction.REPLAN


def test_unknown_outcome_never_retries():
    error = ToolError(ErrorCode.OUTCOME_UNKNOWN, "Cannot establish outcome", True, {"retry_safe": True})
    decision = Replanner().evaluate_result(
        plan(), result(status=ExecutionStatus.UNKNOWN, error=error)
    )
    assert decision.action == ReplanAction.NEEDS_ATTENTION


def test_timeout_never_retries_blindly():
    error = ToolError(ErrorCode.EXECUTION_TIMEOUT, "Timed out", True, {"retry_safe": True})
    decision = Replanner().evaluate_result(
        plan(), result(status=ExecutionStatus.TIMED_OUT, error=error)
    )
    assert decision.action == ReplanAction.NEEDS_ATTENTION


def test_retry_requires_retryable_and_explicit_retry_safe():
    planner = Replanner(max_retries=2)
    safe_error = ToolError(ErrorCode.INTERNAL_ERROR, "Transient", True, {"retry_safe": True})
    unsafe_error = ToolError(ErrorCode.INTERNAL_ERROR, "Transient", True, {"retry_safe": False})
    assert planner.evaluate_result(plan(), result(error=safe_error)).action == ReplanAction.RETRY
    assert planner.evaluate_result(plan(), result(error=unsafe_error)).action == ReplanAction.REPLAN


def test_retry_limit_is_bounded():
    error = ToolError(ErrorCode.INTERNAL_ERROR, "Transient", True, {"retry_safe": True})
    decision = Replanner(max_retries=2).evaluate_result(
        plan(), result(error=error), retries_already_used=2
    )
    assert decision.action == ReplanAction.REPLAN


def test_rejection_and_cancellation_stop_without_retry():
    replanner = Replanner()
    rejected = ToolError(ErrorCode.APPROVAL_REJECTED, "User rejected", False)
    cancelled = ToolError(ErrorCode.EXECUTION_CANCELLED, "Cancelled", False)
    assert replanner.evaluate_result(plan(), result(error=rejected)).action == ReplanAction.STOP
    assert replanner.evaluate_result(
        plan(), result(status=ExecutionStatus.CANCELLED, error=cancelled)
    ).action == ReplanAction.STOP


def test_result_must_belong_to_plan():
    wrong_task = ToolResult("other-task", "step-1", ExecutionStatus.FAILED, False)
    with pytest.raises(PlannerError, match="task_id"):
        Replanner().evaluate_result(plan(), wrong_task)
    wrong_step = ToolResult("task-1", "unknown", ExecutionStatus.FAILED, False)
    with pytest.raises(PlannerError, match="unknown step_id"):
        Replanner().evaluate_result(plan(), wrong_step)


def test_replan_checks_context_has_workspace():
    context = TaskContext(workspace_id="ws", workspace_path="")
    with pytest.raises(PlannerError, match="workspace"):
        Replanner().replan(plan(), result(), context)


def test_approval_required_waits_instead_of_retrying():
    error = ToolError(ErrorCode.APPROVAL_REQUIRED, "Approval required", True, {"retry_safe": True})
    decision = Replanner().evaluate_result(plan(), result(error=error))
    assert decision.action == ReplanAction.WAIT_FOR_APPROVAL
