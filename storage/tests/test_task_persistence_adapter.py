
"""Focused tests for Person 4's task persistence adapter."""

from shared.contracts import (
    ExecutionStatus,
    PlanStep as ContractPlanStep,
    ToolError,
    ToolResult as ContractToolResult,
)
from shared.enums import StepStatus
from storage.task_persistence_adapter import TaskPersistenceAdapter
from shared.schemas import ToolResult as StoredToolResult


def make_adapter_with_step(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    adapter = TaskPersistenceAdapter(tmp_path / "tasks.sqlite3")
    task_id = "adapter-test-task"
    step_id = "adapter-test-step"

    adapter.create_task(task_id, workspace, "Run a test task")
    adapter.save_plan(
        task_id,
        [
            ContractPlanStep(
                step_id=step_id,
                tool="terminal.run",
                arguments={"command": ["python", "--version"]},
            )
        ],
    )
    return adapter, task_id, step_id


def test_retry_attempt_number_increments_from_saved_history(tmp_path):
    adapter, task_id, step_id = make_adapter_with_step(tmp_path)

    try:
        first_id = adapter.start_execution(task_id, step_id)
        first = adapter.storage.executions.get(first_id)

        assert first.attempt_number == 1

        # Finish the first attempt before starting a retry.
        adapter.storage.executions.complete(
            first_id,
            StoredToolResult(
                task_id=task_id,
                step_id=step_id,
                status=StepStatus.FAILED,
                success=False,
                output="",
            ),
        )

        second_id = adapter.start_execution(task_id, step_id)
        second = adapter.storage.executions.get(second_id)

        assert second.attempt_number == 2
    finally:
        adapter.close()


def test_timeout_is_persisted_as_failed_with_timeout_error(tmp_path):
    adapter, task_id, step_id = make_adapter_with_step(tmp_path)

    try:
        execution_id = adapter.start_execution(task_id, step_id)

        timeout_result = ContractToolResult(
            task_id=task_id,
            step_id=step_id,
            status=ExecutionStatus.TIMED_OUT,
            success=False,
            error=ToolError(
                code="EXECUTION_TIMEOUT",
                message="Execution timed out",
                retryable=True,
                details={},
            ),
        )

        adapter.complete_execution(timeout_result)

        saved = adapter.storage.executions.get(execution_id)

        assert saved.status is StepStatus.FAILED
        assert saved.success is False
        assert saved.error is not None
        assert saved.error.code == "EXECUTION_TIMEOUT"
    finally:
        adapter.close()
