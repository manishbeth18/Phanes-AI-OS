from dataclasses import FrozenInstanceError

import pytest

from shared.context import TaskContext
from shared.contracts import Plan, PlanStep
from planner.planner import Planner, PlannerError, eligible_steps, validate_plan


def context():
    return TaskContext(workspace_id="ws-1", workspace_path="/tmp/project")


def test_create_plan_uses_shared_contract_and_existing_task_id():
    def provider(*, goal, workspace_id, context, task_id):
        assert goal == "Inspect project"
        assert workspace_id == "ws-1"
        assert context.workspace_id == "ws-1"
        return Plan(task_id=task_id, steps=[PlanStep("s1", "filesystem.list_directory")])

    plan = Planner(provider).create_plan(" Inspect project ", "ws-1", context(), "task-1")
    assert plan.task_id == "task-1"
    assert isinstance(plan.steps[0], PlanStep)


def test_create_plan_rejects_context_workspace_mismatch():
    planner = Planner(lambda **kwargs: Plan(kwargs["task_id"], []))
    with pytest.raises(PlannerError, match="does not match"):
        planner.create_plan("goal", "other", context())


def test_create_plan_rejects_wrong_task_id_from_provider():
    planner = Planner(lambda **kwargs: Plan("wrong-id", []))
    with pytest.raises(PlannerError, match="does not match"):
        planner.create_plan("goal", "ws-1", context(), "task-1")


def test_validate_plan_rejects_duplicate_ids():
    plan = Plan("t1", [PlanStep("s1", "a"), PlanStep("s1", "b")])
    with pytest.raises(PlannerError, match="duplicate step_id"):
        validate_plan(plan)


def test_validate_plan_rejects_missing_dependency():
    plan = Plan("t1", [PlanStep("s1", "a", depends_on=["missing"])])
    with pytest.raises(PlannerError, match="missing dependencies"):
        validate_plan(plan)


def test_validate_plan_rejects_dependency_cycle():
    plan = Plan("t1", [
        PlanStep("s1", "a", depends_on=["s2"]),
        PlanStep("s2", "b", depends_on=["s1"]),
    ])
    with pytest.raises(PlannerError, match="cycle"):
        validate_plan(plan)


def test_validate_plan_checks_registry_only_when_provided():
    plan = Plan("t1", [PlanStep("s1", "real.tool")])
    validate_plan(plan)
    with pytest.raises(PlannerError, match="unknown tool"):
        validate_plan(plan, allowed_tools={"other.tool"})


def test_eligible_steps_respects_dependencies_and_blocked_closure():
    plan = Plan("t1", [
        PlanStep("s1", "a"),
        PlanStep("s2", "b", depends_on=["s1"]),
        PlanStep("s3", "c", depends_on=["s2"]),
    ])
    assert [s.step_id for s in eligible_steps(plan, [])] == ["s1"]
    assert [s.step_id for s in eligible_steps(plan, ["s1"])] == ["s2"]
    assert eligible_steps(plan, ["s1"], blocked_step_ids={"s2"}) == ()


def test_create_plan_rejects_explicit_empty_task_id():
    planner = Planner(lambda **kwargs: Plan(kwargs["task_id"], []))
    with pytest.raises(PlannerError, match="task_id must be a non-empty string"):
        planner.create_plan("goal", "ws-1", context(), "")
