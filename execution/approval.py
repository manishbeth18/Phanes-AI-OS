
"""Action-bound, expiring, single-use approvals for PHANES."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApprovalRecord:
    """An approval bound to one exact action."""

    tool_name: str
    arguments_hash: str
    expires_at: float


class ApprovalManager:
    """Issue and consume short-lived, single-use approval tokens."""

    def __init__(self) -> None:
        self._approvals: dict[str, ApprovalRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        """Create a stable fingerprint of JSON-compatible arguments."""
        try:
            serialized = json.dumps(
                arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Approval arguments must be JSON-serializable."
            ) from exc

        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def issue(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ttl_seconds: int = 60,
    ) -> str:
        """Issue a token for a specific tool call.

        The caller must obtain trusted user confirmation before issuing
        an approval. This method does not itself ask the user.
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")

        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a dictionary.")

        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= 3600
        ):
            raise ValueError("ttl_seconds must be between 1 and 3600.")

        record = ApprovalRecord(
            tool_name=tool_name.strip(),
            arguments_hash=self._arguments_hash(arguments),
            expires_at=time.monotonic() + ttl_seconds,
        )

        with self._lock:
            token = secrets.token_urlsafe(32)

            while token in self._approvals:
                token = secrets.token_urlsafe(32)

            self._approvals[token] = record

        return token

    def consume(
        self,
        token: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        """Validate and consume a token exactly once.

        A token is burned when presented for a different action, preventing
        repeated attempts to reuse it with altered arguments.
        """
        if not isinstance(token, str) or not token:
            return False

        try:
            fingerprint = self._arguments_hash(arguments)
        except ValueError:
            return False

        with self._lock:
            record = self._approvals.pop(token, None)

        if record is None:
            return False

        if time.monotonic() >= record.expires_at:
            return False

        return (
            record.tool_name == tool_name
            and secrets.compare_digest(record.arguments_hash, fingerprint)
        )
