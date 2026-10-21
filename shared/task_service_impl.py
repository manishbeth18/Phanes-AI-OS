"""Concrete PHANES task service.

Connects the planner, context provider, execution engine, approval gate,
and UI-facing events defined in shared/service.py.

Task lifecycle service with optional SQLite persistence.

Persistence is injected so callers that do not configure a database retain
the original in-memory behavior.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from execution.approval_gate import (
    ApprovalGate,
    ApprovalGateError,
    ApprovalResolution,
)
from execution.register_all import create_execution_engine
from execution.tool_registry import ToolSafetyLevel
from shared.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ErrorCode,
    ExecutionStatus,
    Plan,
    PlanStep,
    TaskStatus,
    ToolError,
    ToolResult,
)
from shared.service import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    LogEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TaskEvent,
    TaskFinishedEvent,
    TaskService,
)


PlannerCallable = Callable[[str, str, Any], Plan]
ContextProvider = Callable[[str, str], Any]
EventCallback = Callable[[object], None]


class TaskServiceError(RuntimeError):
    """Raised when a task-service operation cannot be completed."""


class TaskCancelledError(TaskServiceError):
    """Raised internally when a task is cancelled."""


class ApprovalRejectedError(TaskServiceError):
    """Raised internally when a required approval is rejected."""


@dataclass
class _PendingApproval:
    request: ApprovalRequest
    gate: ApprovalGate
    event: threading.Event = field(default_factory=threading.Event)
    resolution: ApprovalResolution | None = None


@dataclass
class _TaskRecord:
    task_id: str
    goal: str
    workspace: str
    status: TaskStatus = TaskStatus.CREATED
    message: str = "Task created."
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime | None = None


class ConcreteTaskService(TaskService):
    """Threaded implementation of the canonical PHANES TaskService protocol.

    Args:
        planner:
            Callable accepting (goal, workspace, context) and returning Plan.
            Adapt Person 1's planner to this interface if necessary.
        context_provider:
            Optional callable accepting (workspace, goal) and returning
            whatever context object the planner expects.
        approval_timeout_seconds:
            Maximum time to wait for a human approval decision.
        repository_root:
            Optional repository path for Git tools. Defaults to workspace.
        persistence:
            Optional TaskPersistenceAdapter-compatible object. When supplied,
            tasks, plans, statuses, and execution results are persisted.
    """

    def __init__(
        self,
        planner: PlannerCallable,
        context_provider: ContextProvider | None = None,
        *,
        approval_timeout_seconds: float = 300.0,
        repository_root: str | None = None,
        persistence: Any | None = None,
    ) -> None:
        if not callable(planner):
            raise TypeError("planner must be callable.")

        if context_provider is not None and not callable(context_provider):
            raise TypeError("context_provider must be callable or None.")

        if (
            isinstance(approval_timeout_seconds, bool)
            or not isinstance(approval_timeout_seconds, (int, float))
            or approval_timeout_seconds <= 0
        ):
            raise ValueError("approval_timeout_seconds must be positive.")

        self._planner = planner
        self._context_provider = context_provider
        self._approval_timeout_seconds = float(approval_timeout_seconds)
        self._repository_root = repository_root
        self._persistence = persistence

        self._lock = threading.RLock()
        self._subscribers: set[EventCallback] = set()
        self._tasks: dict[str, _TaskRecord] = {}
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._approval_gates: dict[str, ApprovalGate] = {}

    # ------------------------------------------------------------------
    # TaskService interface
    # ------------------------------------------------------------------

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Subscribe to task events. Return an unsubscribe function."""
        if not callable(callback):
            raise TypeError("callback must be callable.")

        with self._lock:
            self._subscribers.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.discard(callback)

        return unsubscribe

    def submit(self, goal: str, workspace: str) -> str:
        """Create a task and start planning/execution on a worker thread."""
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string.")

        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a non-empty path.")

        task_id = uuid4().hex
        record = _TaskRecord(
            task_id=task_id,
            goal=goal.strip(),
            workspace=workspace.strip(),
        )

        # Persist before starting the worker, so a submitted task is not lost
        # if the process exits immediately after acceptance.
        if self._persistence is not None:
            self._persistence.create_task(
                task_id,
                record.workspace,
                record.goal,
            )

        with self._lock:
            self._tasks[task_id] = record

        self._emit(
            TaskEvent(
                task_id=task_id,
                status=TaskStatus.CREATED,
                message="Task accepted.",
            )
        )

        worker = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"phanes-task-{task_id[:8]}",
            daemon=True,
        )
        record.thread = worker
        worker.start()

        return task_id

    def cancel(self, task_id: str) -> None:
        """Request cooperative cancellation of a task."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(f"Unknown task ID: {task_id}")

            if record.status in self._terminal_statuses():
                return

            record.cancel_event.set()

            pending = [
                item
                for item in self._pending_approvals.values()
                if item.request.task_id == task_id
            ]

        self._set_status(
            task_id,
            TaskStatus.CANCELLING,
            "Cancellation requested.",
        )

        # Wake any worker waiting for an approval decision.
        for item in pending:
            item.event.set()

    def resolve_approval(self, decision: ApprovalDecision) -> None:
        """Validate a decision and wake the matching task worker."""
        if not isinstance(decision, ApprovalDecision):
            raise TypeError("decision must be an ApprovalDecision.")

        with self._lock:
            pending = self._pending_approvals.get(decision.approval_id)

        if pending is None:
            raise TaskServiceError(
                "Approval request is unknown, expired, or already resolved."
            )

        if (
            decision.task_id != pending.request.task_id
            or decision.step_id != pending.request.step_id
        ):
            raise TaskServiceError(
                "Approval decision does not match the pending request."
            )

        try:
            resolution = pending.gate.resolve(decision)
        except ApprovalGateError as exc:
            raise TaskServiceError(str(exc)) from exc

        with self._lock:
            # Ensure this exact pending request has not been replaced.
            current = self._pending_approvals.get(decision.approval_id)
            if current is not pending:
                raise TaskServiceError(
                    "Approval request is no longer pending."
                )

            pending.resolution = resolution
            self._pending_approvals.pop(decision.approval_id, None)

        self._emit(
            ApprovalResolvedEvent(
                approval_id=resolution.approval_id,
                task_id=resolution.task_id,
                step_id=resolution.step_id,
                approved=resolution.approved,
                message=resolution.message,
            )
        )

        pending.event.set()

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Return the task's current in-memory status."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(f"Unknown task ID: {task_id}")
            return record.status

    # ------------------------------------------------------------------
    # Internal task lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _terminal_statuses() -> set[TaskStatus]:
        return {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_ATTENTION,
        }

    def _emit(self, event: object) -> None:
        """Send an event to subscribers without letting UI errors kill a task."""
        with self._lock:
            subscribers = tuple(self._subscribers)

        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # A broken UI subscriber must not crash task execution.
                continue

    def _log(
        self,
        task_id: str,
        level: str,
        message: str,
        step_id: str | None = None,
    ) -> None:
        self._emit(
            LogEvent(
                task_id=task_id,
                level=level,
                message=message,
                step_id=step_id,
            )
        )

    def _set_status(
        self,
        task_id: str,
        status: TaskStatus,
        message: str,
    ) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return

            record.status = status
            record.message = message

            if status in self._terminal_statuses():
                record.finished_at = datetime.now(timezone.utc)

        if self._persistence is not None:
            self._persistence.update_task_status(task_id, status)

        self._emit(
            TaskEvent(
                task_id=task_id,
                status=status,
                message=message,
            )
        )

    def _finish(
        self,
        task_id: str,
        status: TaskStatus,
        summary: str,
    ) -> None:
        self._set_status(task_id, status, summary)
        self._emit(
            TaskFinishedEvent(
                task_id=task_id,
                status=status,
                summary=summary,
            )
        )

        with self._lock:
            for approval_id, pending in list(
                self._pending_approvals.items()
            ):
                if pending.request.task_id == task_id:
                    self._pending_approvals.pop(approval_id, None)
                    pending.event.set()

            self._approval_gates.pop(task_id, None)

    def _check_cancelled(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks[task_id]

        if record.cancel_event.is_set():
            raise TaskCancelledError("Task was cancelled.")

    def _run_task(self, task_id: str) -> None:
        try:
            with self._lock:
                record = self._tasks[task_id]

            self._set_status(
                task_id,
                TaskStatus.PLANNING,
                "Building a plan.",
            )
            self._log(task_id, "INFO", "Planning started.")

            self._check_cancelled(task_id)

            context = None
            if self._context_provider is not None:
                context = self._context_provider(
                    record.workspace,
                    record.goal,
                )

            plan = self._planner(record.goal, record.workspace, context)
            self._validate_plan(plan)

            if self._persistence is not None:
                self._persistence.save_plan(task_id, plan.steps)

            self._check_cancelled(task_id)

            _, executor = create_execution_engine(
                record.workspace,
                repository_root=self._repository_root,
            )
            gate = ApprovalGate(executor.approval_manager)

            with self._lock:
                self._approval_gates[task_id] = gate

            self._set_status(
                task_id,
                TaskStatus.READY,
                f"Plan ready: {len(plan.steps)} step(s).",
            )
            self._log(
                task_id,
                "INFO",
                f"Plan contains {len(plan.steps)} step(s).",
            )

            if not plan.steps:
                self._finish(
                    task_id,
                    TaskStatus.SUCCEEDED,
                    "Plan completed; no execution steps were required.",
                )
                return

            self._set_status(
                task_id,
                TaskStatus.RUNNING,
                "Executing the plan.",
            )

            results: dict[str, ToolResult] = {}

            for step in plan.steps:
                self._check_cancelled(task_id)

                failed_dependencies = [
                    dependency
                    for dependency in step.depends_on
                    if dependency not in results
                    or not results[dependency].success
                ]

                if failed_dependencies:
                    result = self._make_failure(
                        task_id,
                        step,
                        ErrorCode.DEPENDENCY_FAILED,
                        "Step skipped because these dependencies failed: "
                        + ", ".join(failed_dependencies),
                    )
                    results[step.step_id] = result
                    if self._persistence is not None:
                        self._persistence.complete_execution(result)
                    self._emit(StepFinishedEvent(result=result))
                    self._log(
                        task_id,
                        "WARNING",
                        result.error.message if result.error else
                        "Dependency failed.",
                        step.step_id,
                    )
                    continue

                self._emit(
                    StepStartedEvent(
                        task_id=task_id,
                        step_id=step.step_id,
                        tool=step.tool,
                    )
                )
                self._log(
                    task_id,
                    "INFO",
                    step.description or f"Executing {step.tool}.",
                    step.step_id,
                )

                try:
                    approval_token = None
                    needs_approval = self._requires_approval(
                        executor,
                        step,
                    )

                    if needs_approval:
                        approval_token = self._request_approval(
                            task_id=task_id,
                            step=step,
                            gate=gate,
                            record=record,
                        )

                    self._check_cancelled(task_id)

                    if self._persistence is not None:
                        self._persistence.start_execution(
                            task_id,
                            step.step_id,
                        )

                    result = executor.execute(
                        step.tool,
                        step.arguments,
                        requires_approval=needs_approval,
                        approval_token=approval_token,
                        task_id=task_id,
                        step_id=step.step_id,
                    )

                except ApprovalRejectedError as exc:
                    result = self._make_failure(
                        task_id,
                        step,
                        ErrorCode.APPROVAL_REJECTED,
                        str(exc),
                    )

                except TimeoutError as exc:
                    result = self._make_failure(
                        task_id,
                        step,
                        ErrorCode.APPROVAL_EXPIRED,
                        str(exc),
                    )

                except TaskCancelledError:
                    raise

                except Exception as exc:
                    result = self._make_failure(
                        task_id,
                        step,
                        ErrorCode.INTERNAL_ERROR,
                        f"{type(exc).__name__}: {exc}",
                    )

                if self._persistence is not None:
                    self._persistence.complete_execution(result)

                results[step.step_id] = result
                self._emit(StepFinishedEvent(result=result))

                if result.success:
                    self._log(
                        task_id,
                        "INFO",
                        f"{step.tool} completed.",
                        step.step_id,
                    )
                else:
                    error_message = (
                        result.error.message
                        if result.error is not None
                        else "Tool execution failed."
                    )
                    self._log(
                        task_id,
                        "ERROR",
                        error_message,
                        step.step_id,
                    )

            self._check_cancelled(task_id)

            succeeded = sum(1 for item in results.values() if item.success)
            failed = len(results) - succeeded

            if failed == 0:
                self._finish(
                    task_id,
                    TaskStatus.SUCCEEDED,
                    f"Completed all {succeeded} step(s).",
                )
            else:
                self._finish(
                    task_id,
                    TaskStatus.FAILED,
                    f"Finished with {succeeded} successful and "
                    f"{failed} failed/skipped step(s).",
                )

        except TaskCancelledError:
            self._finish(
                task_id,
                TaskStatus.CANCELLED,
                "Task cancelled.",
            )

        except Exception as exc:
            self._log(
                task_id,
                "ERROR",
                f"Task failed: {type(exc).__name__}: {exc}",
            )
            self._finish(
                task_id,
                TaskStatus.FAILED,
                f"Task failed: {type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # Plan validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_plan(plan: Plan) -> None:
        if not isinstance(plan, Plan):
            raise TypeError(
                "Planner must return shared.contracts.Plan."
            )

        if not isinstance(plan.steps, list):
            raise TypeError("Plan.steps must be a list.")

        step_ids: set[str] = set()

        for step in plan.steps:
            if not isinstance(step, PlanStep):
                raise TypeError("Every plan step must be a PlanStep.")

            if not isinstance(step.step_id, str) or not step.step_id.strip():
                raise ValueError("Every step must have a non-empty step_id.")

            if step.step_id in step_ids:
                raise ValueError(f"Duplicate step ID: {step.step_id}")

            if not isinstance(step.tool, str) or not step.tool.strip():
                raise ValueError(
                    f"Step {step.step_id} has an invalid tool name."
                )

            if not isinstance(step.arguments, dict):
                raise TypeError(
                    f"Arguments for step {step.step_id} must be a dict."
                )

            if not isinstance(step.depends_on, list):
                raise TypeError(
                    f"Dependencies for step {step.step_id} must be a list."
                )

            step_ids.add(step.step_id)

        for step in plan.steps:
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    raise ValueError(
                        f"Step {step.step_id} depends on unknown step "
                        f"{dependency}."
                    )
                if dependency == step.step_id:
                    raise ValueError(
                        f"Step {step.step_id} cannot depend on itself."
                    )

        # Detect cycles before any tool can execute.
        dependencies = {
            step.step_id: set(step.depends_on)
            for step in plan.steps
        }
        resolved: set[str] = set()

        while dependencies:
            ready = {
                step_id
                for step_id, deps in dependencies.items()
                if deps.issubset(resolved)
            }

            if not ready:
                raise ValueError(
                    "Plan contains a dependency cycle."
                )

            resolved.update(ready)
            for step_id in ready:
                dependencies.pop(step_id)

    # ------------------------------------------------------------------
    # Approval and execution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _requires_approval(executor: Any, step: PlanStep) -> bool:
        """Combine planner intent with registered safety enforcement."""
        if step.requires_approval:
            return True

        # Executor.approval_required checks registered safety metadata and
        # policy defaults. Check the actual arguments as well, since some
        # policy decisions depend on the requested operation.
        if executor.approval_required(step.tool):
            return True

        namespace = step.tool.split(".", 1)[0].lower()
        enforced_namespaces = getattr(
            executor,
            "_POLICY_ENFORCED_NAMESPACES",
            set(),
        )

        if namespace in enforced_namespaces:
            if executor.safety_policy.requires_approval(
                step.tool,
                step.arguments,
            ):
                return True

        metadata = executor.registry.get_metadata(step.tool)
        return metadata.safety_level is ToolSafetyLevel.APPROVAL_REQUIRED

    def _request_approval(
        self,
        *,
        task_id: str,
        step: PlanStep,
        gate: ApprovalGate,
        record: _TaskRecord,
    ) -> str:
        """Publish a request, wait for a decision, and return its bound token."""
        self._check_cancelled(task_id)

        now = datetime.now(timezone.utc)
        approval_id = uuid4().hex

        request = ApprovalRequest(
            approval_id=approval_id,
            task_id=task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments=step.arguments,
            summary=(
                step.description.strip()
                if step.description.strip()
                else f"Approve execution of {step.tool}"
            ),
            expires_at=now + timedelta(
                seconds=self._approval_timeout_seconds
            ),
        )

        # Register with the backend gate before exposing the request to the UI.
        gate.register_request(request)

        pending = _PendingApproval(
            request=request,
            gate=gate,
        )

        with self._lock:
            self._pending_approvals[approval_id] = pending

        self._set_status(
            task_id,
            TaskStatus.WAITING_APPROVAL,
            f"Waiting for approval: {step.tool}.",
        )
        self._emit(ApprovalRequestedEvent(request=request))

        deadline = time.monotonic() + self._approval_timeout_seconds

        try:
            while True:
                self._check_cancelled(task_id)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Approval timed out for {step.tool}."
                    )

                if pending.event.wait(timeout=min(remaining, 0.25)):
                    self._check_cancelled(task_id)

                    resolution = pending.resolution
                    if resolution is None:
                        # The event may have been set by cancellation.
                        continue

                    if not resolution.approved:
                        raise ApprovalRejectedError(
                            f"Approval rejected for {step.tool}."
                        )

                    if not resolution.token:
                        raise TaskServiceError(
                            "Approved request did not receive a token."
                        )

                    return resolution.token

        finally:
            with self._lock:
                self._pending_approvals.pop(approval_id, None)

            # Do not retain the pending approval in the UI service.
            # The gate itself enforces its own expiry and single-use rules.

    @staticmethod
    def _make_failure(
        task_id: str,
        step: PlanStep,
        code: ErrorCode,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            task_id=task_id,
            step_id=step.step_id,
            tool_name=step.tool,
            status=ExecutionStatus.FAILED,
            success=False,
            error=ToolError(
                code=code,
                message=message,
                retryable=False,
                details={"tool_name": step.tool},
            ),
            verified=False,
        )
