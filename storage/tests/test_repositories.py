"""Tests for the repository APIs in storage.repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.enums import StepStatus, TaskStatus
from shared.exceptions import ConstraintViolationError, RecordNotFoundError
from shared.schemas import ExecutionRecord, ToolError, new_id
from storage.repositories import Storage
from storage.tests.conftest import Seed
from storage.tests.factories import (
    make_result,
    make_running_execution,
    make_step,
    make_task,
    make_workspace,
)


# ---- workspaces ------------------------------------------------------------


def test_workspace_upsert_and_get_round_trip(storage: Storage, tmp_path: Path) -> None:
    created = storage.workspaces.upsert(make_workspace(tmp_path / "proj", name="proj"))
    assert storage.workspaces.get(created.workspace_id) == created
    assert created.root_path == tmp_path / "proj"
    assert created.created_at.utcoffset().total_seconds() == 0


def test_workspace_upsert_updates_existing_and_keeps_created_at(
    storage: Storage, tmp_path: Path
) -> None:
    first = storage.workspaces.upsert(make_workspace(tmp_path / "a", name="old"))
    renamed = first.model_copy(update={"name": "new", "root_path": tmp_path / "b"})
    second = storage.workspaces.upsert(renamed)
    assert second.workspace_id == first.workspace_id
    assert second.name == "new"
    assert second.root_path == tmp_path / "b"
    assert second.created_at == first.created_at
    assert len(storage.workspaces.list()) == 1


def test_workspace_get_by_path(storage: Storage, tmp_path: Path) -> None:
    created = storage.workspaces.upsert(make_workspace(tmp_path / "proj"))
    assert storage.workspaces.get_by_path(tmp_path / "proj") == created
    assert storage.workspaces.get_by_path(tmp_path / "other") is None


def test_workspace_list_returns_all(storage: Storage, tmp_path: Path) -> None:
    a = storage.workspaces.upsert(make_workspace(tmp_path / "a", name="a"))
    b = storage.workspaces.upsert(make_workspace(tmp_path / "b", name="b"))
    assert {w.workspace_id for w in storage.workspaces.list()} == {
        a.workspace_id,
        b.workspace_id,
    }


def test_two_workspaces_cannot_share_a_root_path(storage: Storage, tmp_path: Path) -> None:
    storage.workspaces.upsert(make_workspace(tmp_path / "same"))
    with pytest.raises(ConstraintViolationError):
        storage.workspaces.upsert(make_workspace(tmp_path / "same"))
    assert len(storage.workspaces.list()) == 1


def test_workspace_get_missing_raises(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.workspaces.get("nope")


def test_naive_timestamp_is_rejected_and_not_saved(storage: Storage, tmp_path: Path) -> None:
    from datetime import datetime

    workspace = make_workspace(tmp_path / "p", created_at=datetime(2026, 1, 1))
    with pytest.raises(ConstraintViolationError):
        storage.workspaces.upsert(workspace)
    assert storage.workspaces.list() == []


# ---- tasks ------------------------------------------------------------


def test_task_create_and_get_round_trip(storage: Storage, tmp_path: Path) -> None:
    workspace = storage.workspaces.upsert(make_workspace(tmp_path / "p"))
    created = storage.tasks.create(make_task(workspace.workspace_id, goal="fix the bug"))
    assert created.status is TaskStatus.PENDING
    assert storage.tasks.get(created.task_id) == created
    assert created.goal == "fix the bug"


def test_task_create_requires_existing_workspace(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.tasks.create(make_task("missing-workspace"))


def test_task_duplicate_id_is_rejected(storage: Storage, seeded: Seed) -> None:
    with pytest.raises(ConstraintViolationError):
        storage.tasks.create(seeded.task)


def test_task_update_status_persists_and_touches_updated_at(
    storage: Storage, seeded: Seed
) -> None:
    updated = storage.tasks.update_status(seeded.task.task_id, TaskStatus.RUNNING)
    assert updated.status is TaskStatus.RUNNING
    assert updated.updated_at >= seeded.task.updated_at
    assert updated.created_at == seeded.task.created_at
    assert storage.tasks.get(seeded.task.task_id).status is TaskStatus.RUNNING


def test_task_update_status_missing_raises(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.tasks.update_status("nope", TaskStatus.RUNNING)


def test_task_get_missing_raises(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.tasks.get("nope")


def test_task_list_by_status(storage: Storage, seeded: Seed) -> None:
    other = storage.tasks.create(make_task(seeded.workspace.workspace_id))
    storage.tasks.update_status(other.task_id, TaskStatus.RUNNING)
    pending = storage.tasks.list_by_status([TaskStatus.PENDING])
    running = storage.tasks.list_by_status([TaskStatus.RUNNING, TaskStatus.PLANNING])
    assert [t.task_id for t in pending] == [seeded.task.task_id]
    assert [t.task_id for t in running] == [other.task_id]
    assert storage.tasks.list_by_status([]) == []


# ---- plan steps ------------------------------------------------------------


def test_steps_create_many_round_trip_in_order(storage: Storage, seeded: Seed) -> None:
    first = make_step(
        tool="fs.read",
        arguments={"path": "README.md", "nested": {"a": [1, 2]}},
        depends_on=[seeded.step.step_id],
        requires_approval=True,
    )
    second = make_step(tool="git.status")
    saved = storage.steps.create_many(seeded.task.task_id, [first, second])
    assert saved == [first, second]
    listed = storage.steps.list_for_task(seeded.task.task_id)
    assert [s.step_id for s in listed] == [
        seeded.step.step_id,
        first.step_id,
        second.step_id,
    ]
    assert listed[1].arguments == {"path": "README.md", "nested": {"a": [1, 2]}}
    assert listed[1].depends_on == [seeded.step.step_id]
    assert listed[1].requires_approval is True


def test_steps_create_many_is_atomic(storage: Storage, seeded: Seed) -> None:
    good = make_step()
    duplicate_of_existing = make_step(step_id=seeded.step.step_id)
    with pytest.raises(ConstraintViolationError):
        storage.steps.create_many(seeded.task.task_id, [good, duplicate_of_existing])
    remaining = storage.steps.list_for_task(seeded.task.task_id)
    assert [s.step_id for s in remaining] == [seeded.step.step_id]


def test_step_create_with_explicit_position_and_duplicate_position(
    storage: Storage, seeded: Seed
) -> None:
    storage.steps.create(seeded.task.task_id, make_step(), position=5)
    with pytest.raises(ConstraintViolationError):
        storage.steps.create(seeded.task.task_id, make_step(), position=5)


def test_step_create_requires_existing_task(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.steps.create("missing-task", make_step(), position=0)
    with pytest.raises(RecordNotFoundError):
        storage.steps.create_many("missing-task", [make_step()])


def test_step_update_status_persists(storage: Storage, seeded: Seed) -> None:
    updated = storage.steps.update_status(
        seeded.task.task_id, seeded.step.step_id, StepStatus.RUNNING
    )
    assert updated.status is StepStatus.RUNNING
    assert storage.steps.get(seeded.step.step_id).status is StepStatus.RUNNING


def test_step_update_status_rejects_wrong_task_or_missing(
    storage: Storage, seeded: Seed
) -> None:
    other_task = storage.tasks.create(make_task(seeded.workspace.workspace_id))
    with pytest.raises(RecordNotFoundError):
        storage.steps.update_status(other_task.task_id, seeded.step.step_id, StepStatus.RUNNING)
    with pytest.raises(RecordNotFoundError):
        storage.steps.update_status(seeded.task.task_id, "nope", StepStatus.RUNNING)
    assert storage.steps.get(seeded.step.step_id).status is StepStatus.PENDING


def test_step_get_and_list_missing_raise(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.steps.get("nope")
    with pytest.raises(RecordNotFoundError):
        storage.steps.list_for_task("nope")


def test_step_with_unserializable_arguments_is_rejected(
    storage: Storage, seeded: Seed
) -> None:
    bad = make_step(arguments={"value": object()})
    with pytest.raises(ConstraintViolationError):
        storage.steps.create_many(seeded.task.task_id, [bad])
    assert len(storage.steps.list_for_task(seeded.task.task_id)) == 1


# ---- executions: create before running ------------------------------------------------------------


def test_execution_is_created_running_with_no_outcome(storage: Storage, seeded: Seed) -> None:
    created = storage.executions.create(
        make_running_execution(seeded.task.task_id, seeded.step.step_id)
    )
    assert created.status is StepStatus.RUNNING
    assert created.success is None
    assert created.verified is False
    assert created.finished_at is None
    assert created.verification_details is None
    assert created.started_at.utcoffset().total_seconds() == 0
    assert storage.executions.list_for_task(seeded.task.task_id) == [created]


def test_execution_create_rejects_records_that_already_have_an_outcome(
    storage: Storage, seeded: Seed
) -> None:
    base = {"task_id": seeded.task.task_id, "step_id": seeded.step.step_id}
    bad_records: list[ExecutionRecord] = [
        make_running_execution(**base, status=StepStatus.COMPLETED),
        make_running_execution(**base, success=True),
        make_running_execution(**base, verified=True, verification_details="checked"),
        make_running_execution(**base, output="already has output"),
        make_running_execution(**base, exit_code=0),
    ]
    for bad in bad_records:
        with pytest.raises(ConstraintViolationError):
            storage.executions.create(bad)
    assert storage.executions.list_for_task(seeded.task.task_id) == []


def test_execution_create_requires_step_in_the_same_task(
    storage: Storage, seeded: Seed
) -> None:
    other_task = storage.tasks.create(make_task(seeded.workspace.workspace_id))
    with pytest.raises(RecordNotFoundError):
        storage.executions.create(make_running_execution("missing-task", seeded.step.step_id))
    with pytest.raises(RecordNotFoundError):
        storage.executions.create(make_running_execution(seeded.task.task_id, "missing-step"))
    # The step exists, but belongs to a different task.
    with pytest.raises(RecordNotFoundError):
        storage.executions.create(
            make_running_execution(other_task.task_id, seeded.step.step_id)
        )


def test_execution_attempt_numbers_are_unique_per_step(storage: Storage, seeded: Seed) -> None:
    storage.executions.create(make_running_execution(seeded.task.task_id, seeded.step.step_id, 1))
    with pytest.raises(ConstraintViolationError):
        storage.executions.create(
            make_running_execution(seeded.task.task_id, seeded.step.step_id, 1)
        )
    second = storage.executions.create(
        make_running_execution(seeded.task.task_id, seeded.step.step_id, 2)
    )
    assert second.attempt_number == 2


def test_execution_attempt_number_must_be_positive(storage: Storage, seeded: Seed) -> None:
    with pytest.raises(ConstraintViolationError):
        storage.executions.create(
            make_running_execution(seeded.task.task_id, seeded.step.step_id, 0)
        )


def test_execution_get_and_list_missing_raise(storage: Storage) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.executions.get("nope")
    with pytest.raises(RecordNotFoundError):
        storage.executions.list_for_task("nope")


# ---- executions: complete and verification state ------------------------------------------------------------


def _running(storage: Storage, seeded: Seed) -> ExecutionRecord:
    return storage.executions.create(
        make_running_execution(seeded.task.task_id, seeded.step.step_id)
    )


def test_complete_stores_verified_success(storage: Storage, seeded: Seed) -> None:
    running = _running(storage, seeded)
    result = make_result(
        seeded.task.task_id,
        seeded.step.step_id,
        output="3 passed",
        verified=True,
        verification_details="re-read file and compared checksum",
    )
    done = storage.executions.complete(running.execution_id, result, exit_code=0)
    assert done.status is StepStatus.COMPLETED
    assert done.success is True
    assert done.verified is True
    assert done.verification_details == "re-read file and compared checksum"
    assert done.output == "3 passed"
    assert done.exit_code == 0
    assert done.error is None
    assert done.finished_at is not None and done.finished_at >= running.started_at
    assert storage.executions.get(running.execution_id) == done


def test_complete_does_not_upgrade_unverified_results(storage: Storage, seeded: Seed) -> None:
    running = _running(storage, seeded)
    done = storage.executions.complete(
        running.execution_id,
        make_result(seeded.task.task_id, seeded.step.step_id, success=True),
    )
    assert done.success is True  # stored exactly as reported
    assert done.verified is False  # never inferred from the absence of an exception
    assert done.verification_details is None


def test_complete_stores_verified_failure_with_structured_error(
    storage: Storage, seeded: Seed
) -> None:
    running = _running(storage, seeded)
    error = ToolError(
        code="TEST_FAILURE",
        message="2 tests failed",
        retryable=False,
        details={"failed": 2, "tests": ["a", "b"]},
    )
    result = make_result(
        seeded.task.task_id,
        seeded.step.step_id,
        status=StepStatus.FAILED,
        success=False,
        output="2 failed",
        error=error,
        verified=True,
        verification_details="pytest exit code was 1",
    )
    done = storage.executions.complete(running.execution_id, result, exit_code=1)
    assert done.status is StepStatus.FAILED
    assert done.success is False
    assert done.verified is True  # verified failure: verified != succeeded
    assert done.error == error
    assert done.exit_code == 1


def test_complete_can_record_an_unknown_outcome(storage: Storage, seeded: Seed) -> None:
    running = _running(storage, seeded)
    done = storage.executions.complete(
        running.execution_id,
        make_result(
            seeded.task.task_id,
            seeded.step.step_id,
            status=StepStatus.UNKNOWN,
            success=False,
        ),
    )
    assert done.status is StepStatus.UNKNOWN
    assert done.verified is False


def test_complete_rejects_verified_without_details_and_stays_running(
    storage: Storage, seeded: Seed
) -> None:
    running = _running(storage, seeded)
    for details in (None, "", "   "):
        with pytest.raises(ConstraintViolationError):
            storage.executions.complete(
                running.execution_id,
                make_result(
                    seeded.task.task_id,
                    seeded.step.step_id,
                    verified=True,
                    verification_details=details,
                ),
            )
    still = storage.executions.get(running.execution_id)
    assert still.status is StepStatus.RUNNING
    assert still.success is None
    assert still.verified is False


def test_complete_rejects_non_final_statuses(storage: Storage, seeded: Seed) -> None:
    running = _running(storage, seeded)
    for status in (StepStatus.RUNNING, StepStatus.PENDING, StepStatus.WAITING_FOR_APPROVAL):
        with pytest.raises(ConstraintViolationError):
            storage.executions.complete(
                running.execution_id,
                make_result(seeded.task.task_id, seeded.step.step_id, status=status),
            )
    assert storage.executions.get(running.execution_id).status is StepStatus.RUNNING


def test_complete_can_only_happen_once(storage: Storage, seeded: Seed) -> None:
    running = _running(storage, seeded)
    first = storage.executions.complete(
        running.execution_id,
        make_result(seeded.task.task_id, seeded.step.step_id, output="first"),
    )
    with pytest.raises(ConstraintViolationError):
        storage.executions.complete(
            running.execution_id,
            make_result(
                seeded.task.task_id,
                seeded.step.step_id,
                status=StepStatus.FAILED,
                success=False,
                output="second",
            ),
        )
    assert storage.executions.get(running.execution_id) == first


def test_complete_rejects_missing_execution_and_mismatched_ids(
    storage: Storage, seeded: Seed
) -> None:
    with pytest.raises(RecordNotFoundError):
        storage.executions.complete(
            new_id(), make_result(seeded.task.task_id, seeded.step.step_id)
        )
    running = _running(storage, seeded)
    with pytest.raises(ConstraintViolationError):
        storage.executions.complete(
            running.execution_id, make_result(seeded.task.task_id, "other-step")
        )
    with pytest.raises(ConstraintViolationError):
        storage.executions.complete(
            running.execution_id, make_result("other-task", seeded.step.step_id)
        )
    assert storage.executions.get(running.execution_id).status is StepStatus.RUNNING
