"""Shared pytest fixtures for storage tests.

Every database lives under pytest's ``tmp_path``; tests never touch a real
application database.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from shared.schemas import PlanStep, TaskRecord, Workspace
from storage.database import Database
from storage.repositories import Storage
from storage.tests.factories import make_step, make_task, make_workspace


@dataclass
class Seed:
    """A workspace with one task and one step already persisted."""

    workspace: Workspace
    task: TaskRecord
    step: PlanStep


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "phanes_test.sqlite3"


@pytest.fixture
def db(db_path: Path) -> Iterator[Database]:
    database = Database(db_path)
    database.open()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def storage(db_path: Path) -> Iterator[Storage]:
    opened = Storage.open(db_path)
    yield opened
    opened.close()


@pytest.fixture
def seeded(storage: Storage, tmp_path: Path) -> Seed:
    workspace = storage.workspaces.upsert(make_workspace(tmp_path / "project"))
    task = storage.tasks.create(make_task(workspace.workspace_id))
    step = storage.steps.create_many(task.task_id, [make_step()])[0]
    return Seed(workspace=workspace, task=task, step=step)
