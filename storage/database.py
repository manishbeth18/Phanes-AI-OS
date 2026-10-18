"""SQLite connection, transaction and migration management.

STORAGE-INTERNAL: ``Database`` and its raw ``sqlite3.Connection`` must not be
used outside ``storage/``. Other modules obtain data only through the
repository APIs (spec section 8). ``storage/__init__.py`` therefore does not
export this class.

Design decisions (all PROVISIONAL, none are specified by the architecture):

* One connection guarded by a re-entrant lock. Every operation runs inside
  ``transaction()``, so access from several threads is serialized rather than
  assumed safe. Revisit if the UI needs concurrent readers.
* The connection runs in autocommit mode (``isolation_level=None``) and this
  class issues BEGIN/COMMIT/ROLLBACK itself, so transaction boundaries are
  explicit instead of depending on sqlite3's implicit behaviour.
* Nested ``transaction()`` calls become SAVEPOINTs.
* ``sqlite3`` errors are translated into the shared storage exceptions with
  the original error chained.
* WAL journal mode is intentionally not enabled yet.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from shared.exceptions import (
    ConstraintViolationError,
    MigrationError,
    StorageError,
)
from shared.schemas import utc_now
from storage.models import to_db_timestamp

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_BUSY_TIMEOUT_SECONDS = 5.0
_MIGRATION_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    sql: str


def _translate_error(exc: BaseException) -> BaseException:
    """Map sqlite3 errors to shared storage errors; leave others unchanged."""
    if isinstance(exc, sqlite3.IntegrityError):
        return ConstraintViolationError(f"constraint violated: {exc}")
    if isinstance(exc, sqlite3.Error):
        return StorageError(f"database error: {exc}")
    return exc


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into individual statements.

    ``sqlite3.Connection.executescript`` commits implicitly, which would break
    per-migration atomicity, so statements are executed one by one instead.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    leftover = "\n".join(
        line
        for line in buffer.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    if leftover:
        raise MigrationError(f"migration ends with an unterminated statement: {leftover!r}")
    return statements


class Database:
    """Owns one SQLite connection, its transactions and the schema version."""

    def __init__(self, db_path: Path, migrations_dir: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._migrations_dir = (
            DEFAULT_MIGRATIONS_DIR if migrations_dir is None else Path(migrations_dir)
        )
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._tx_depth = 0

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> None:
        """Open the connection and verify that foreign keys are enforced."""
        with self._lock:
            if self._conn is not None:
                return
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(self._db_path),
                    timeout=_BUSY_TIMEOUT_SECONDS,
                    isolation_level=None,
                    check_same_thread=False,
                )
            except (OSError, sqlite3.Error) as exc:
                raise StorageError(
                    f"cannot open database at {self._db_path}: {exc}"
                ) from exc
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            except sqlite3.Error as exc:
                conn.close()
                raise StorageError(
                    f"cannot configure database connection: {exc}"
                ) from exc
            if enabled != 1:
                conn.close()
                raise StorageError(
                    "SQLite foreign key enforcement could not be enabled"
                )
            self._conn = conn

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        with self._lock:
            if self._conn is None:
                return
            if self._tx_depth > 0:
                raise StorageError("cannot close the database inside a transaction")
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                raise StorageError(f"error while closing database: {exc}") from exc
            finally:
                self._conn = None

    def __enter__(self) -> "Database":
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("database is not open")
        return self._conn

    def foreign_keys_enabled(self) -> bool:
        """Report whether the live connection enforces foreign keys."""
        with self.transaction(write=False) as conn:
            return bool(conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)

    # ---- transactions ----------------------------------------------------

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        """Run a block atomically; commit on success, roll back on any error.

        ``write=True`` takes the write lock up front (BEGIN IMMEDIATE).
        Nested calls use SAVEPOINTs, so an inner failure rolls back only the
        inner block. Errors raised inside the block are translated to shared
        storage exceptions; non-sqlite exceptions pass through unchanged after
        the rollback.
        """
        with self._lock:
            conn = self._require_connection()
            nested = self._tx_depth > 0
            savepoint = f"phanes_sp_{self._tx_depth}"
            if nested:
                begin_sql = f"SAVEPOINT {savepoint}"
            else:
                begin_sql = "BEGIN IMMEDIATE" if write else "BEGIN"
            try:
                conn.execute(begin_sql)
            except sqlite3.Error as exc:
                raise _translate_error(exc) from exc
            self._tx_depth += 1
            try:
                try:
                    yield conn
                except BaseException as exc:
                    self._rollback(conn, nested, savepoint)
                    translated = _translate_error(exc)
                    if translated is exc:
                        raise
                    raise translated from exc
                else:
                    self._commit(conn, nested, savepoint)
            finally:
                self._tx_depth -= 1

    @staticmethod
    def _rollback(conn: sqlite3.Connection, nested: bool, savepoint: str) -> None:
        try:
            if nested:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            else:
                conn.execute("ROLLBACK")
        except sqlite3.Error as exc:
            raise StorageError(f"rollback failed: {exc}") from exc

    def _commit(self, conn: sqlite3.Connection, nested: bool, savepoint: str) -> None:
        try:
            conn.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
        except sqlite3.Error as exc:
            try:
                self._rollback(conn, nested, savepoint)
            except StorageError:
                # The commit failure below is the error worth reporting; the
                # connection may already have rolled itself back.
                pass
            raise _translate_error(exc) from exc

    # ---- migrations ------------------------------------------------------

    def schema_version(self) -> int:
        """Return the highest applied migration version (0 if none)."""
        with self.transaction(write=False) as conn:
            applied = self._read_applied(conn)
        return applied[-1][0] if applied else 0

    def migrate(self) -> int:
        """Apply every pending migration in order; return the schema version.

        Works on a fresh database and on an existing supported database.
        Each migration runs in its own transaction together with its
        ``schema_migrations`` row, so a failed migration leaves no partial
        schema and is not recorded as applied.
        """
        migrations = self._discover_migrations()
        with self._lock:
            with self.transaction(write=False) as conn:
                applied = self._read_applied(conn)
            self._validate_applied(applied, migrations)
            for migration in migrations[len(applied):]:
                self._apply(migration)
        return self.schema_version()

    def _discover_migrations(self) -> list[_Migration]:
        if not self._migrations_dir.is_dir():
            raise MigrationError(
                f"migrations directory not found: {self._migrations_dir}"
            )
        found: list[_Migration] = []
        for path in sorted(self._migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILE.match(path.name)
            if match is None:
                raise MigrationError(
                    f"migration file name must look like 0001_name.sql: {path.name}"
                )
            try:
                sql = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise MigrationError(f"cannot read migration {path.name}: {exc}") from exc
            found.append(_Migration(int(match.group(1)), match.group(2), sql))
        if not found:
            raise MigrationError(f"no migrations found in {self._migrations_dir}")
        versions = [m.version for m in found]
        if versions != list(range(1, len(found) + 1)):
            raise MigrationError(
                f"migration versions must be contiguous starting at 1; found {versions}"
            )
        return found

    @staticmethod
    def _read_applied(conn: sqlite3.Connection) -> list[tuple[int, str]]:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_migrations",),
        ).fetchone()
        if exists is None:
            return []
        rows = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [(int(row["version"]), str(row["name"])) for row in rows]

    @staticmethod
    def _validate_applied(
        applied: list[tuple[int, str]], migrations: list[_Migration]
    ) -> None:
        if len(applied) > len(migrations):
            raise MigrationError(
                f"database schema version {applied[-1][0]} is newer than the "
                f"newest known migration ({migrations[-1].version}); "
                "update the application before opening this database"
            )
        for index, (version, name) in enumerate(applied):
            expected = migrations[index]
            if version != expected.version or name != expected.name:
                raise MigrationError(
                    f"applied migration {version:04d}_{name} does not match "
                    f"{expected.version:04d}_{expected.name}"
                )

    def _apply(self, migration: _Migration) -> None:
        label = f"{migration.version:04d}_{migration.name}"
        statements = _split_statements(migration.sql)
        try:
            with self.transaction() as conn:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (migration.version, migration.name, to_db_timestamp(utc_now())),
                )
        except MigrationError:
            raise
        except StorageError as exc:
            raise MigrationError(f"migration {label} failed: {exc}") from exc
