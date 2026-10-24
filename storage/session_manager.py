
"""Session recovery for Phanes AI-OS.

Recovers persisted task state without automatically rerunning tools.
"""

from dataclasses import dataclass

from shared.enums import StepStatus, TaskStatus
from shared.schemas import ExecutionRecord, PlanStep, TaskRecord
from storage.repositories import Storage


@dataclass
class RecoveredSession:
    """Saved state needed to inspect and resume a task safely."""

    task: TaskRecord
    steps: list[PlanStep]
    executions: list[ExecutionRecord]

    @property
    def interrupted_executions(self) -> list[ExecutionRecord]:
        """Attempts whose outcome still needs reconciliation."""
        return [
            execution
            for execution in self.executions
            if execution.status == StepStatus.RUNNING
        ]


class SessionManager:
    """Loads saved task state after an application restart."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def recover(self, task_id: str) -> RecoveredSession:
        """Load a task, its plan and execution history.

        This method does not rerun commands or change saved statuses.
        """
        task = self._storage.tasks.get(task_id)
        steps = self._storage.steps.list_for_task(task_id)
        executions = self._storage.executions.list_for_task(task_id)

        return RecoveredSession(
            task=task,
            steps=steps,
            executions=executions,
        )

    def find_recoverable_tasks(self) -> list[TaskRecord]:
        """Find tasks that may need recovery after a restart."""
        return self._storage.tasks.list_by_status(
            [
                TaskStatus.PENDING,
                TaskStatus.PLANNING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_FOR_APPROVAL,
            ]
        )
