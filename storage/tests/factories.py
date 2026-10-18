"""Small helpers that build valid shared models for storage tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.enums import StepStatus
from shared.schemas import (
    ExecutionRecord,
    PlanStep,
    TaskRecord,
    ToolResult,
    Workspace,
    new_id,
)


def make_workspace(root: Path, name: str = "demo", **overrides: Any) -> Workspace:
    values: dict[str, Any] = {
        "workspace_id": new_id(),
        "root_path": root,
        "name": name,
    }
    values.update(overrides)
    return Workspace(**values)


def make_task(workspace_id: str, goal: str = "run the tests", **overrides: Any) -> TaskRecord:
    values: dict[str, Any] = {
        "task_id": new_id(),
        "workspace_id": workspace_id,
        "goal": goal,
    }
    values.update(overrides)
    return TaskRecord(**values)


def make_step(tool: str = "terminal.run", **overrides: Any) -> PlanStep:
    values: dict[str, Any] = {
        "step_id": new_id(),
        "tool": tool,
        "arguments": {"command": ["python", "--version"]},
    }
    values.update(overrides)
    return PlanStep(**values)


def make_running_execution(
    task_id: str, step_id: str, attempt_number: int = 1, **overrides: Any
) -> ExecutionRecord:
    values: dict[str, Any] = {
        "execution_id": new_id(),
        "task_id": task_id,
        "step_id": step_id,
        "attempt_number": attempt_number,
    }
    values.update(overrides)
    return ExecutionRecord(**values)


def make_result(task_id: str, step_id: str, **overrides: Any) -> ToolResult:
    values: dict[str, Any] = {
        "task_id": task_id,
        "step_id": step_id,
        "status": StepStatus.COMPLETED,
        "success": True,
        "output": "ok",
    }
    values.update(overrides)
    return ToolResult(**values)
