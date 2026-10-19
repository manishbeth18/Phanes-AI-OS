"""Validate shared approval decisions before issuing execution tokens.

This gate correlates a decision with a previously registered ApprovalRequest.
It does not authenticate a human or replace a trusted UI/service boundary.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from execution.approval import ApprovalManager
from shared.contracts import ApprovalDecision, ApprovalRequest


class ApprovalGateError(ValueError):
    """Raised when an approval request or decision is invalid."""


@dataclass(frozen=True)
class ApprovalResolution:
    approval_id: str
    task_id: str
    step_id: str
    approved: bool
    token: str | None = None
    message: str = ""


@dataclass(frozen=True)
class _PendingApproval:
    task_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    expires_at_utc: datetime
    deadline_monotonic: float


class ApprovalGate:
    """Correlate pending requests with decisions and issue tokens only on approval.

    Call ``register_request`` when the backend creates/publishes a real pending
    ApprovalRequest. Call ``resolve`` when the service receives a decision.
    The caller must still ensure the decision came from a trusted user action.
    """

    def __init__(
        self,
        approval_manager: ApprovalManager | None = None,
        *,
        max_pending: int = 1000,
    ) -> None:
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer.")
        self._manager = approval_manager or ApprovalManager()
        self._max_pending = max_pending
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _aware_utc(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ApprovalGateError(f"{field_name} must be a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _copy_json_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ApprovalGateError("Approval request arguments must be a dictionary.")
        try:
            encoded = json.dumps(
                arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            copied = json.loads(encoded)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ApprovalGateError(
                "Approval request arguments must be JSON-serializable."
            ) from exc
        if not isinstance(copied, dict):
            raise ApprovalGateError("Approval request arguments must be a dictionary.")
        return copied

    def register_request(self, request: ApprovalRequest) -> None:
        """Register the exact request that was presented for user approval."""
        if not isinstance(request, ApprovalRequest):
            raise ApprovalGateError("request must be an ApprovalRequest.")

        for field_name in ("approval_id", "task_id", "step_id", "tool", "summary"):
            value = getattr(request, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ApprovalGateError(f"{field_name} must be a non-empty string.")

        expires_at = self._aware_utc(request.expires_at, "expires_at")
        now_utc = datetime.now(timezone.utc)
        remaining = (expires_at - now_utc).total_seconds()
        if remaining <= 0:
            raise ApprovalGateError("Cannot register an expired approval request.")

        arguments = self._copy_json_arguments(request.arguments)
        pending = _PendingApproval(
            task_id=request.task_id,
            step_id=request.step_id,
            tool_name=request.tool.strip(),
            arguments=arguments,
            expires_at_utc=expires_at,
            deadline_monotonic=time.monotonic() + remaining,
        )

        with self._lock:
            self._purge_expired_locked(time.monotonic())
            if request.approval_id in self._pending:
                raise ApprovalGateError("Approval ID is already pending.")
            if len(self._pending) >= self._max_pending:
                raise ApprovalGateError("Pending approval capacity is full.")
            self._pending[request.approval_id] = pending

    def _purge_expired_locked(self, now_monotonic: float) -> None:
        expired = [
            approval_id
            for approval_id, pending in self._pending.items()
            if now_monotonic >= pending.deadline_monotonic
        ]
        for approval_id in expired:
            self._pending.pop(approval_id, None)

    def resolve(self, decision: ApprovalDecision) -> ApprovalResolution:
        """Resolve one pending request; issue an action-bound token only on approval.

        Invalid ID combinations do not consume a valid pending request. A
        correctly correlated, valid decision is single-use, whether approved
        or rejected. Expiration is checked against the backend's own clock.
        """
        if not isinstance(decision, ApprovalDecision):
            raise ApprovalGateError("decision must be an ApprovalDecision.")
        for field_name in ("approval_id", "task_id", "step_id"):
            value = getattr(decision, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ApprovalGateError(f"{field_name} must be a non-empty string.")
        if type(decision.approved) is not bool:
            raise ApprovalGateError("approved must be a boolean.")
        self._aware_utc(decision.decided_at, "decided_at")

        with self._lock:
            now = time.monotonic()
            self._purge_expired_locked(now)
            pending = self._pending.get(decision.approval_id)
            if pending is None:
                raise ApprovalGateError("Approval request is unknown, expired, or resolved.")
            if (
                decision.task_id != pending.task_id
                or decision.step_id != pending.step_id
            ):
                raise ApprovalGateError("Decision does not match the pending request.")

            # Consume the correlated request before issuing a token. This keeps
            # duplicate/concurrent decisions from creating multiple tokens.
            self._pending.pop(decision.approval_id, None)
            remaining = pending.deadline_monotonic - now

        if remaining <= 0:
            raise ApprovalGateError("Approval request has expired.")

        if not decision.approved:
            return ApprovalResolution(
                approval_id=decision.approval_id,
                task_id=decision.task_id,
                step_id=decision.step_id,
                approved=False,
                message="Approval rejected.",
            )

        ttl_seconds = min(3600, int(remaining))
        if ttl_seconds < 1:
            raise ApprovalGateError("Approval request has too little time remaining.")

        try:
            token = self._manager.issue(
                pending.tool_name,
                pending.arguments,
                ttl_seconds=ttl_seconds,
            )
        except (ValueError, RuntimeError) as exc:
            raise ApprovalGateError("Could not issue an execution approval token.") from exc

        return ApprovalResolution(
            approval_id=decision.approval_id,
            task_id=decision.task_id,
            step_id=decision.step_id,
            approved=True,
            token=token,
            message="Approval validated; action-bound token issued.",
        )
