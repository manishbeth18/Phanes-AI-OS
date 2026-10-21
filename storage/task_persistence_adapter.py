"""Adapter between PHANES canonical contracts and SQLite storage."""

from pathlib import Path
from uuid import uuid4

from shared.contracts import (
    ExecutionStatus,
    StepStatus as ContractStepStatus,
    TaskStatus as ContractTaskStatus,
    ToolResult as ContractToolResult,
)
from shared.enums import StepStatus, TaskStatus
from shared.schemas import (
    ExecutionRecord,
    PlanStep as StoredPlanStep,
    TaskRecord,
    ToolError as StoredToolError,
    ToolResult as StoredToolResult,
    Workspace,
    utc_now,
)
from storage import Storage


TASK_STATUS_MAP = {
    ContractTaskStatus.CREATED: TaskStatus.PENDING,
    ContractTaskStatus.PLANNING: TaskStatus.PLANNING,
    ContractTaskStatus.READY: TaskStatus.PENDING,
    ContractTaskStatus.RUNNING: TaskStatus.RUNNING,
    ContractTaskStatus.WAITING_APPROVAL: TaskStatus.WAITING_FOR_APPROVAL,
    ContractTaskStatus.CANCELLING: TaskStatus.RUNNING,
    ContractTaskStatus.SUCCEEDED: TaskStatus.COMPLETED,
    ContractTaskStatus.FAILED: TaskStatus.FAILED,
    ContractTaskStatus.CANCELLED: TaskStatus.CANCELLED,
    ContractTaskStatus.NEEDS_ATTENTION: TaskStatus.FAILED,
}

STEP_STATUS_MAP = {
    ContractStepStatus.PENDING: StepStatus.PENDING,
    ContractStepStatus.READY: StepStatus.PENDING,
    ContractStepStatus.RUNNING: StepStatus.RUNNING,
    ContractStepStatus.WAITING_APPROVAL: StepStatus.WAITING_FOR_APPROVAL,
    ContractStepStatus.SUCCEEDED: StepStatus.COMPLETED,
    ContractStepStatus.FAILED: StepStatus.FAILED,
    ContractStepStatus.SKIPPED: StepStatus.SKIPPED,
    ContractStepStatus.CANCELLED: StepStatus.CANCELLED,
    ContractStepStatus.UNKNOWN: StepStatus.UNKNOWN,
}


class TaskPersistenceAdapter:
    """Persist tasks, plans, and execution results using SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.storage = Storage.open(Path(db_path))
        self._execution_ids: dict[tuple[str, str], str] = {}

    def close(self) -> None:
        """Close the underlying SQLite storage."""
        self.storage.close()

    def ensure_workspace(self, workspace_path: str | Path) -> str:
        """Create or retrieve a workspace record."""
        root = Path(workspace_path).expanduser().resolve()

        existing = self.storage.workspaces.get_by_path(root)
        if existing is not None:
            return existing.workspace_id

        now = utc_now()
        workspace_id = str(uuid4())

        workspace = Workspace(
            workspace_id=workspace_id,
            root_path=root,
            name=root.name or str(root),
            created_at=now,
            last_opened_at=now,
        )

        self.storage.workspaces.upsert(workspace)

        # Read back the stored workspace.
        existing = self.storage.workspaces.get_by_path(root)
        if existing is not None:
            return existing.workspace_id

        return workspace_id

    def create_task(
        self,
        task_id: str,
        workspace_path: str | Path,
        goal: str,
    ) -> None:
        """Persist a new task before its worker starts."""
        workspace_id = self.ensure_workspace(workspace_path)
        now = utc_now()

        self.storage.tasks.create(
            TaskRecord(
                task_id=task_id,
                workspace_id=workspace_id,
                goal=goal,
                status=TaskStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )

    def update_task_status(
        self,
        task_id: str,
        status: ContractTaskStatus,
    ) -> None:
        """Persist a task status."""
        self.storage.tasks.update_status(
            task_id,
            TASK_STATUS_MAP[status],
        )

    def save_plan(self, task_id: str, steps: list) -> None:
        """Save plan steps with their initial status set to PENDING."""
        stored_steps = [
            StoredPlanStep(
                step_id=step.step_id,
                tool=step.tool,
                arguments=step.arguments,
                status=StepStatus.PENDING,
                depends_on=step.depends_on,
                requires_approval=step.requires_approval,
            )
            for step in steps
        ]

        if stored_steps:
            self.storage.steps.create_many(task_id, stored_steps)

    def update_step_status(
        self,
        task_id: str,
        step_id: str,
        status: ContractStepStatus,
    ) -> None:
        """Update a stored step's status."""
        self.storage.steps.update_status(
            task_id,
            step_id,
            STEP_STATUS_MAP[status],
        )

    def start_execution(
        self,
        task_id: str,
        step_id: str,
        attempt_number: int = 1,
    ) -> str:
        """Record an execution attempt before running a tool."""
        execution_id = str(uuid4())

        self.storage.executions.create(
            ExecutionRecord(
                execution_id=execution_id,
                task_id=task_id,
                step_id=step_id,
                attempt_number=attempt_number,
                status=StepStatus.RUNNING,
                started_at=utc_now(),
            )
        )

        self._execution_ids[(task_id, step_id)] = execution_id

        self.update_step_status(
            task_id,
            step_id,
            ContractStepStatus.RUNNING,
        )

        return execution_id

    @staticmethod
    def _error_code_value(error: object) -> str | None:
        """Return an error code as a string."""
        if error is None:
            return None

        code = getattr(error, "code", None)

        if hasattr(code, "value"):
            return str(code.value)

        if isinstance(code, str):
            return code

        return None

    def complete_execution(self, result: ContractToolResult) -> None:
        """Persist an execution result and update its plan-step status."""
        execution_id = self._execution_ids.pop(
            (result.task_id, result.step_id),
            None,
        )

        error_code = self._error_code_value(result.error)

        if result.status == ExecutionStatus.SUCCEEDED and result.success:
            stored_status = StepStatus.COMPLETED
        elif result.status == ExecutionStatus.CANCELLED:
            stored_status = StepStatus.CANCELLED
        elif result.status == ExecutionStatus.UNKNOWN:
            stored_status = StepStatus.UNKNOWN
        elif error_code == "DEPENDENCY_FAILED":
            stored_status = StepStatus.SKIPPED
        else:
            stored_status = StepStatus.FAILED

        # Dependency failures may not have a separate execution attempt.
        if execution_id is None:
            self.storage.steps.update_status(
                result.task_id,
                result.step_id,
                stored_status,
            )
            return

        stored_error = None

        if result.error is not None:
            stored_error = StoredToolError(
                code=str(error_code or "UNKNOWN_ERROR"),
                message=result.error.message,
                retryable=result.error.retryable,
                details=result.error.details,
            )

        stored_result = StoredToolResult(
            task_id=result.task_id,
            step_id=result.step_id,
            status=stored_status,
            success=result.success,
            output="" if result.output is None else str(result.output),
            error=stored_error,
            verified=result.verified,
            verification_details=result.verification_details,
        )

        self.storage.executions.complete(
            execution_id,
            stored_result,
        )

        self.storage.steps.update_status(
            result.task_id,
            result.step_id,
            stored_status,
        )