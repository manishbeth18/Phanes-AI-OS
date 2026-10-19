"""Coordinate approval decisions with execution of the exact approved action.

This is an execution-layer coordinator, not a UI or a complete TaskService.
The caller must authenticate the source of ApprovalDecision before passing it
here. A decision matching a pending request is not, by itself, proof of human
identity or intent.
"""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass

from execution.approval import ApprovalManager
from execution.approval_gate import (
    ApprovalGate,
    ApprovalGateError,
    ApprovalResolution,
)
from execution.executor import Executor
from shared.contracts import ApprovalDecision, ApprovalRequest, ToolResult


@dataclass(frozen=True)
class ApprovalExecutionOutcome:
    """Result of resolving an approval and, when approved, executing its action."""

    resolution: ApprovalResolution
    tool_result: ToolResult | None = None


class ApprovalRuntime:
    """Bind pending approval requests, decisions, tokens, and tool execution.

    One ApprovalManager must be shared by the gate and executor. On approval,
    the original registered tool and copied arguments are executed; the caller
    cannot substitute a different action at decision time.
    """

    def __init__(self, executor: Executor, gate: ApprovalGate) -> None:
        if not isinstance(executor, Executor):
            raise TypeError("executor must be an Executor instance.")
        if not isinstance(gate, ApprovalGate):
            raise TypeError("gate must be an ApprovalGate instance.")
        # ApprovalGate currently stores its manager as ``_manager``. Keep this
        # identity check aligned with that implementation; a public accessor can
        # replace this coupling if the gate's API is expanded later.
        if gate._manager is not executor.approval_manager:
            raise ValueError(
                "ApprovalGate and Executor must share the same ApprovalManager."
            )

        self.executor = executor
        self.gate = gate
        self._pending_requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def register_request(self, request: ApprovalRequest) -> None:
        """Register a request with the gate and retain an isolated action copy."""
        if not isinstance(request, ApprovalRequest):
            raise TypeError("request must be an ApprovalRequest instance.")

        # The gate validates JSON compatibility and stores its own argument copy.
        # Deep-copy here too so later caller mutation cannot change the action.
        try:
            saved_request = copy.deepcopy(request)
            saved_request.arguments  # force access before mutating internal state
        except (TypeError, ValueError, RecursionError) as exc:
            raise ApprovalGateError("Approval request could not be copied.") from exc

        with self._lock:
            if request.approval_id in self._pending_requests:
                raise ApprovalGateError("Duplicate approval ID.")
            self.gate.register_request(request)
            self._pending_requests[request.approval_id] = saved_request

    def resolve_and_execute(
        self, decision: ApprovalDecision
    ) -> ApprovalExecutionOutcome:
        """Resolve one decision; execute only its originally registered action.

        Invalid/mismatched decisions raise ApprovalGateError and never execute
        a tool. A rejection returns an outcome with no tool result.
        """
        if not isinstance(decision, ApprovalDecision):
            raise TypeError("decision must be an ApprovalDecision instance.")

        # ApprovalGate atomically consumes the pending request before returning.
        resolution = self.gate.resolve(decision)

        with self._lock:
            request = self._pending_requests.pop(resolution.approval_id, None)

        if request is None:
            # Fail closed if the coordinator and gate ever become inconsistent.
            raise ApprovalGateError("Pending approval action is unavailable.")

        if not resolution.approved:
            return ApprovalExecutionOutcome(resolution=resolution)

        if not resolution.token:
            raise ApprovalGateError("Approved decision did not produce a token.")

        result = self.executor.execute(
            request.tool,
            copy.deepcopy(request.arguments),
            requires_approval=True,
            approval_token=resolution.token,
            task_id=request.task_id,
            step_id=request.step_id,
        )
        return ApprovalExecutionOutcome(
            resolution=resolution,
            tool_result=result,
        )
