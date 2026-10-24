"""Result evaluation and safe retry/replanning decisions for PHANES."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shared.context import TaskContext
from shared.contracts import (
    ErrorCode,
    ExecutionStatus,
    Plan,
    ToolResult,
)

from planner.planner import PlannerError, validate_plan


class ReplanAction(str, Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    REPLAN = "replan"
    STOP = "stop"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True)
class ReplanDecision:
    """Planner-local decision; it is not an execution or persistence record."""

    action: ReplanAction
    task_id: str
    step_id: str
    reason: str
    retry_attempt: int = 0


class Replanner:
    """Evaluate one execution result and choose the next safe planner action.

    ``max_retries`` counts retries after the initial attempt. A result is only
    retryable when the shared error says ``retryable=True`` AND its ``details``
    explicitly contains ``retry_safe=True``. The latter is a compatibility
    convention carried in the existing details dictionary; uncertain outcomes
    are never retried regardless of that flag.
    """

    def __init__(self, *, max_retries: int = 2) -> None:
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.max_retries = max_retries

    def evaluate_result(
        self,
        plan: Plan,
        result: ToolResult,
        *,
        retries_already_used: int = 0,
    ) -> ReplanDecision:
        """Evaluate a result without executing tools or mutating the plan."""
        validate_plan(plan)
        if result.task_id != plan.task_id:
            raise PlannerError("ToolResult task_id does not match Plan.task_id")
        plan_step_ids = {step.step_id for step in plan.steps}
        if result.step_id not in plan_step_ids:
            raise PlannerError(
                f"ToolResult references unknown step_id: {result.step_id}"
            )
        if retries_already_used < 0:
            raise ValueError("retries_already_used must be non-negative")

        if result.success and result.status == ExecutionStatus.SUCCEEDED:
            if not result.verified:
                return ReplanDecision(
                    ReplanAction.REPLAN,
                    result.task_id,
                    result.step_id,
                    "Operation reported success but was not verified; verify the outcome before dependent work proceeds.",
                )
            return ReplanDecision(
                ReplanAction.CONTINUE,
                result.task_id,
                result.step_id,
                "Execution succeeded and its outcome was verified.",
            )

        error_code = result.error.code if result.error is not None else None
        uncertain = (
            result.status in (ExecutionStatus.UNKNOWN, ExecutionStatus.TIMED_OUT)
            or error_code == ErrorCode.OUTCOME_UNKNOWN
            or error_code == ErrorCode.EXECUTION_TIMEOUT
            or error_code == ErrorCode.VERIFICATION_FAILED
        )
        if uncertain:
            return ReplanDecision(
                ReplanAction.NEEDS_ATTENTION,
                result.task_id,
                result.step_id,
                "Execution outcome is uncertain; reconcile actual state before retrying.",
            )

        if (
            result.status == ExecutionStatus.CANCELLED
            or error_code == ErrorCode.EXECUTION_CANCELLED
        ):
            return ReplanDecision(
                ReplanAction.STOP,
                result.task_id,
                result.step_id,
                "Execution was cancelled; do not schedule an automatic retry.",
            )

        if error_code == ErrorCode.APPROVAL_REQUIRED:
            return ReplanDecision(
                ReplanAction.WAIT_FOR_APPROVAL,
                result.task_id,
                result.step_id,
                "Execution is blocked pending backend approval; do not retry or bypass authorization.",
            )

        if error_code in (
            ErrorCode.APPROVAL_REJECTED,
            ErrorCode.APPROVAL_INVALID,
            ErrorCode.APPROVAL_EXPIRED,
            ErrorCode.PERMISSION_DENIED,
            ErrorCode.TOOL_BLOCKED,
        ):
            return ReplanDecision(
                ReplanAction.STOP,
                result.task_id,
                result.step_id,
                "Approval or authorization did not permit execution.",
            )

        retry_safe = bool(
            result.error is not None
            and result.error.details.get("retry_safe") is True
        )
        retryable = bool(result.error is not None and result.error.retryable)
        if retryable and retry_safe and retries_already_used < self.max_retries:
            return ReplanDecision(
                ReplanAction.RETRY,
                result.task_id,
                result.step_id,
                "Error is retryable and the operation is explicitly marked safe to repeat.",
                retry_attempt=retries_already_used + 1,
            )

        return ReplanDecision(
            ReplanAction.REPLAN,
            result.task_id,
            result.step_id,
            "Execution failed without a safe automatic retry; revise the plan or stop.",
        )

    def replan(
        self,
        plan: Plan,
        result: ToolResult,
        context: TaskContext,
        *,
        retries_already_used: int = 0,
    ) -> ReplanDecision:
        """Compatibility entry point for planner orchestration.

        ``context`` is accepted for the orchestrator-facing workflow and is
        checked against the plan's task context when relevant. This method
        returns a decision, not a replacement ``Plan``: the canonical shared
        contract does not define a replanning-result model or a planner service
        protocol. The orchestrator can request a fresh plan after updating
        context with the failed result and prior completed work.
        """
        if not context.workspace_id or not context.workspace_path:
            raise PlannerError("TaskContext must identify a workspace")
        return self.evaluate_result(
            plan, result, retries_already_used=retries_already_used
        )
