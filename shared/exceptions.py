"""Shared exception types for Phanes AI-OS.

Every exception carries a stable ``code`` string so callers can react without
parsing messages. ``StorageError.code`` uses the specification's recommended
``DATABASE_ERROR`` code (section 7).

PROVISIONAL: the specification lists ``shared/exceptions.py`` but does not
define its contents. ``PhanesError`` and the codes ``RECORD_NOT_FOUND``,
``CONSTRAINT_VIOLATION`` and ``MIGRATION_ERROR`` are my proposals and must be
agreed with the team (see docs/DATABASE_DESIGN.md notes in the hand-off).
"""


class PhanesError(Exception):
    """Base class for Phanes AI-OS errors that carry a stable code."""

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StorageError(PhanesError):
    """A persistence operation failed. Nothing may be assumed to be saved."""

    code = "DATABASE_ERROR"


class RecordNotFoundError(StorageError):
    """A record that the operation requires does not exist."""

    code = "RECORD_NOT_FOUND"


class ConstraintViolationError(StorageError):
    """Data violates a database constraint or a storage rule.

    Examples: duplicate identifier, missing parent record, naive timestamp,
    attempt to finalize an execution twice.
    """

    code = "CONSTRAINT_VIOLATION"


class MigrationError(StorageError):
    """A database migration is invalid, inconsistent or failed."""

    code = "MIGRATION_ERROR"
