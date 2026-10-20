"""Plan construction and structural validation for PHANES.

The planner proposes plans only. It never executes tools or treats a planner
approval flag as authorization; the execution backend remains responsible for
policy enforcement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import uuid4

from shared.context import TaskContext
from shared.contracts import Plan, PlanStep


class PlannerError(ValueError):
    """Raised when a requested or generated plan is invalid."""


PlanProvider = Callable[..., Plan]


class Planner:
    """Build a canonical ``Plan`` using an injected planning strategy.

    A provider can be backed by an LLM, a deterministic rule set, or a test
    double. This module intentionally adds no model-provider dependency and
    does not assume a tool catalogue that the shared contract does not define.

    The provider is called with keyword arguments ``goal``, ``workspace_id``,
    ``context`` and ``task_id`` and must return ``shared.contracts.Plan``.
    """

    def __init__(
        self,
        plan_provider: PlanProvider,
        *,
        allowed_tools: Iterable[str] | None = None,
    ) -> None:
        if not callable(plan_provider):
            raise TypeError("plan_provider must be callable")
        self._plan_provider = plan_provider
        self._allowed_tools = (
            frozenset(allowed_tools) if allowed_tools is not None else None
        )

    def create_plan(
        self,
        goal: str,
        workspace_id: str,
        context: TaskContext,
        task_id: str | None = None,
    ) -> Plan:
        """Generate and validate a plan for a user goal.

        ``task_id`` can be supplied by the orchestrator when it has already
        created a persisted task. If omitted, a UUID is generated for a new
        plan. The returned plan always uses the canonical shared ``Plan`` and
        ``PlanStep`` dataclasses.
        """
        if not isinstance(goal, str) or not goal.strip():
            raise PlannerError("goal must be a non-empty string")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise PlannerError("workspace_id must be a non-empty string")
        if not isinstance(context, TaskContext):
            raise PlannerError("context must be a shared TaskContext")
        if context.workspace_id != workspace_id:
            raise PlannerError(
                "workspace_id does not match the supplied TaskContext"
            )
        if not context.workspace_path:
            raise PlannerError("TaskContext.workspace_path must not be empty")

        resolved_task_id = str(uuid4()) if task_id is None else task_id
        if not isinstance(resolved_task_id, str) or not resolved_task_id.strip():
            raise PlannerError("task_id must be a non-empty string")

        plan = self._plan_provider(
            goal=goal.strip(),
            workspace_id=workspace_id,
            context=context,
            task_id=resolved_task_id,
        )
        if not isinstance(plan, Plan):
            raise PlannerError("plan_provider must return shared.contracts.Plan")
        if plan.task_id != resolved_task_id:
            raise PlannerError(
                "generated plan task_id does not match the requested task_id"
            )

        validate_plan(plan, allowed_tools=self._allowed_tools)
        return plan


def validate_plan(
    plan: Plan,
    *,
    allowed_tools: Iterable[str] | None = None,
) -> None:
    """Validate IDs, tools and dependency structure without mutating a plan.

    Unknown tool names are checked only when the caller supplies the current
    registry's tool names. The shared contract itself does not define a tool
    catalogue, so this function does not invent one.
    """
    if not isinstance(plan, Plan):
        raise PlannerError("plan must be a shared.contracts.Plan")
    if not isinstance(plan.task_id, str) or not plan.task_id.strip():
        raise PlannerError("plan.task_id must be a non-empty string")
    if not isinstance(plan.steps, list):
        raise PlannerError("plan.steps must be a list")

    tool_names = frozenset(allowed_tools) if allowed_tools is not None else None
    by_id: dict[str, PlanStep] = {}
    for step in plan.steps:
        if not isinstance(step, PlanStep):
            raise PlannerError("every plan step must be a shared PlanStep")
        if not isinstance(step.step_id, str) or not step.step_id.strip():
            raise PlannerError("step_id must be a non-empty string")
        if step.step_id in by_id:
            raise PlannerError(f"duplicate step_id: {step.step_id}")
        if not isinstance(step.tool, str) or not step.tool.strip():
            raise PlannerError(f"step {step.step_id} has an empty tool name")
        if tool_names is not None and step.tool not in tool_names:
            raise PlannerError(
                f"step {step.step_id} references unknown tool: {step.tool}"
            )
        if not isinstance(step.arguments, dict):
            raise PlannerError(f"step {step.step_id} arguments must be a dict")
        if len(step.depends_on) != len(set(step.depends_on)):
            raise PlannerError(
                f"step {step.step_id} contains duplicate dependencies"
            )
        if step.step_id in step.depends_on:
            raise PlannerError(f"step {step.step_id} depends on itself")
        by_id[step.step_id] = step

    for step in plan.steps:
        missing = [dep for dep in step.depends_on if dep not in by_id]
        if missing:
            raise PlannerError(
                f"step {step.step_id} has missing dependencies: {', '.join(missing)}"
            )

    # Three-colour DFS detects cycles while keeping dependency order irrelevant.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise PlannerError(f"dependency cycle detected at step {step_id}")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id].depends_on:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in by_id:
        visit(step_id)


def eligible_steps(
    plan: Plan,
    completed_step_ids: Iterable[str],
    *,
    blocked_step_ids: Iterable[str] = (),
) -> tuple[PlanStep, ...]:
    """Return steps whose prerequisites completed and which are not blocked.

    The orchestrator supplies persisted/current step state. A step is eligible
    only if every dependency is in ``completed_step_ids`` and neither it nor
    any dependency is listed as blocked. This helper does not execute anything.
    """
    validate_plan(plan)
    completed = set(completed_step_ids)
    blocked = set(blocked_step_ids)
    # A blocked prerequisite blocks all downstream steps, even if an upstream
    # state was accidentally included in completed_step_ids.
    blocked_closure = set(blocked)
    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            if step.step_id not in blocked_closure and any(
                dep in blocked_closure for dep in step.depends_on
            ):
                blocked_closure.add(step.step_id)
                changed = True

    return tuple(
        step
        for step in plan.steps
        if step.step_id not in completed
        and step.step_id not in blocked_closure
        and all(dep in completed for dep in step.depends_on)
    )
