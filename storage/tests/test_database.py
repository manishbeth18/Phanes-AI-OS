"""Tests for storage.database.Database (connections, FKs, transactions, migrations)."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shared.exceptions import (
    ConstraintViolationError,
    MigrationError,
    StorageError,
)
from storage.database import DEFAULT_MIGRATIONS_DIR, Database
from storage.models import from_db_timestamp, to_db_timestamp

EXPECTED_TABLES = {
    "schema_migrations",
    "workspaces",
    "tasks",
    "plan_steps",
    "tool_executions",
}

NOW = "2026-01-01T00:00:00.000000+00:00"


def _table_names(db: Database) -> set[str]:
    with db.transaction(write=False) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row["name"] for row in rows}


def _insert_workspace(conn: sqlite3.Connection, workspace_id: str, path: str) -> None:
    conn.execute(
        "INSERT INTO workspaces (workspace_id, root_path, name, created_at, "
        "last_opened_at) VALUES (?, ?, ?, ?, ?)",
        (workspace_id, path, "w", NOW, NOW),
    )


def _migrations_copy(tmp_path: Path) -> Path:
    """A private copy of the real migrations that a test may extend."""
    target = tmp_path / "migrations"
    target.mkdir()
    shutil.copy(DEFAULT_MIGRATIONS_DIR / "0001_initial.sql", target)
    return target


# ---- migrations ------------------------------------------------------------


def test_fresh_database_migrates_to_version_1(db_path: Path) -> None:
    assert not db_path.exists()
    database = Database(db_path)
    database.open()
    try:
        assert database.schema_version() == 0
        assert database.migrate() == 1
        assert EXPECTED_TABLES <= _table_names(database)
        with database.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations"
            ).fetchall()
        assert [(r["version"], r["name"]) for r in rows] == [(1, "initial")]
        assert from_db_timestamp(rows[0]["applied_at"]).tzinfo is not None
    finally:
        database.close()


def test_migrate_is_idempotent(db: Database) -> None:
    assert db.migrate() == 1
    assert db.migrate() == 1
    with db.transaction(write=False) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 1


def test_new_migration_applies_to_existing_database(tmp_path: Path) -> None:
    migrations = _migrations_copy(tmp_path)
    path = tmp_path / "existing.sqlite3"

    first = Database(path, migrations)
    first.open()
    first.migrate()
    with first.transaction() as conn:
        _insert_workspace(conn, "w1", "C:/keep/me")
    first.close()

    (migrations / "0002_add_note.sql").write_text(
        "CREATE TABLE notes (note_id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )
    second = Database(path, migrations)
    second.open()
    try:
        assert second.migrate() == 2
        assert "notes" in _table_names(second)
        with second.transaction(write=False) as conn:
            kept = conn.execute("SELECT root_path FROM workspaces").fetchall()
        assert [row["root_path"] for row in kept] == ["C:/keep/me"]
    finally:
        second.close()


def test_failed_migration_leaves_no_partial_state(tmp_path: Path) -> None:
    migrations = _migrations_copy(tmp_path)
    (migrations / "0002_broken.sql").write_text(
        "CREATE TABLE half_done (id TEXT PRIMARY KEY);\n"
        "INSERT INTO no_such_table VALUES (1);\n",
        encoding="utf-8",
    )
    database = Database(tmp_path / "broken.sqlite3", migrations)
    database.open()
    try:
        with pytest.raises(MigrationError):
            database.migrate()
        assert database.schema_version() == 1
        assert "half_done" not in _table_names(database)
    finally:
        database.close()


def test_gap_in_migration_versions_is_rejected(tmp_path: Path) -> None:
    migrations = _migrations_copy(tmp_path)
    (migrations / "0003_skipped_two.sql").write_text(
        "CREATE TABLE x (id TEXT);\n", encoding="utf-8"
    )
    database = Database(tmp_path / "gap.sqlite3", migrations)
    database.open()
    try:
        with pytest.raises(MigrationError):
            database.migrate()
        assert database.schema_version() == 0
    finally:
        database.close()


def test_badly_named_migration_file_is_rejected(tmp_path: Path) -> None:
    migrations = _migrations_copy(tmp_path)
    (migrations / "add_stuff.sql").write_text("SELECT 1;\n", encoding="utf-8")
    database = Database(tmp_path / "name.sqlite3", migrations)
    database.open()
    try:
        with pytest.raises(MigrationError):
            database.migrate()
    finally:
        database.close()


def test_missing_migrations_directory_is_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "x.sqlite3", tmp_path / "does_not_exist")
    database.open()
    try:
        with pytest.raises(MigrationError):
            database.migrate()
    finally:
        database.close()


def test_database_newer_than_code_is_rejected(tmp_path: Path) -> None:
    migrations = _migrations_copy(tmp_path)
    (migrations / "0002_extra.sql").write_text(
        "CREATE TABLE extra (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )
    path = tmp_path / "newer.sqlite3"
    newer = Database(path, migrations)
    newer.open()
    newer.migrate()
    newer.close()

    older_code = Database(path)  # only knows migration 0001
    older_code.open()
    try:
        with pytest.raises(MigrationError):
            older_code.migrate()
    finally:
        older_code.close()


# ---- foreign keys ------------------------------------------------------------


def test_foreign_keys_are_enabled_and_verified(db: Database) -> None:
    assert db.foreign_keys_enabled() is True


def test_foreign_key_violation_is_rejected_and_nothing_is_saved(db: Database) -> None:
    with pytest.raises(ConstraintViolationError) as excinfo:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, workspace_id, goal, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("t1", "missing-workspace", "goal", "pending", NOW, NOW),
            )
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_check_constraint_rejects_unknown_status(db: Database) -> None:
    with db.transaction() as conn:
        _insert_workspace(conn, "w1", "C:/p")
    with pytest.raises(ConstraintViolationError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, workspace_id, goal, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("t1", "w1", "goal", "done", NOW, NOW),
            )


# ---- transactions ------------------------------------------------------------


def test_transaction_commits_on_success(db: Database) -> None:
    with db.transaction() as conn:
        _insert_workspace(conn, "w1", "C:/p")
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 1


def test_transaction_rolls_back_on_error_and_reraises_original(db: Database) -> None:
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            _insert_workspace(conn, "w1", "C:/p")
            raise RuntimeError("boom")
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 0


def test_sqlite_errors_become_storage_errors(db: Database) -> None:
    with pytest.raises(StorageError) as excinfo:
        with db.transaction() as conn:
            conn.execute("SELECT * FROM table_that_does_not_exist")
    assert excinfo.value.code == "DATABASE_ERROR"
    assert isinstance(excinfo.value.__cause__, sqlite3.Error)


def test_nested_transaction_rolls_back_only_inner_block(db: Database) -> None:
    with db.transaction() as conn:
        _insert_workspace(conn, "outer", "C:/outer")
        with pytest.raises(RuntimeError):
            with db.transaction() as inner:
                _insert_workspace(inner, "inner", "C:/inner")
                raise RuntimeError("inner failure")
    with db.transaction(write=False) as conn:
        ids = [r["workspace_id"] for r in conn.execute("SELECT workspace_id FROM workspaces")]
    assert ids == ["outer"]


def test_outer_failure_rolls_back_nested_work_too(db: Database) -> None:
    with pytest.raises(RuntimeError):
        with db.transaction():
            with db.transaction() as inner:
                _insert_workspace(inner, "inner", "C:/inner")
            raise RuntimeError("outer failure")
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 0


# ---- lifecycle ------------------------------------------------------------


def test_using_a_closed_database_raises_storage_error(db_path: Path) -> None:
    database = Database(db_path)
    database.open()
    database.migrate()
    database.close()
    with pytest.raises(StorageError):
        with database.transaction():
            pass
    database.close()  # closing twice is harmless


def test_using_a_never_opened_database_raises_storage_error(db_path: Path) -> None:
    with pytest.raises(StorageError):
        Database(db_path).schema_version()


def test_cannot_close_inside_a_transaction(db: Database) -> None:
    with db.transaction():
        with pytest.raises(StorageError):
            db.close()


def test_committed_data_survives_close_and_reopen(db_path: Path) -> None:
    first = Database(db_path)
    first.open()
    first.migrate()
    with first.transaction() as conn:
        _insert_workspace(conn, "w1", "C:/persisted")
    first.close()

    second = Database(db_path)
    second.open()
    try:
        assert second.migrate() == 1  # no re-migration needed
        with second.transaction(write=False) as conn:
            rows = conn.execute("SELECT root_path FROM workspaces").fetchall()
        assert [row["root_path"] for row in rows] == ["C:/persisted"]
        assert second.foreign_keys_enabled() is True
    finally:
        second.close()


# ---- timestamp helpers ------------------------------------------------------


def test_timestamps_round_trip_as_utc() -> None:
    original = datetime(2026, 5, 17, 12, 30, 45, 123456, tzinfo=timezone.utc)
    stored = to_db_timestamp(original)
    assert stored.endswith("+00:00")
    assert from_db_timestamp(stored) == original


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ConstraintViolationError):
        to_db_timestamp(datetime(2026, 5, 17, 12, 30, 45))
