"""Tests for action-bound PHANES approvals."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import math
import threading
import time

import pytest

from execution.approval import ApprovalManager


def test_token_is_bound_to_tool_name():
    manager = ApprovalManager()
    token = manager.issue("filesystem.read_file", {"path": "notes.txt"})

    assert manager.consume(
        token, "filesystem.read_file", {"path": "notes.txt"}
    ) is True


def test_token_cannot_authorize_different_tool():
    manager = ApprovalManager()
    token = manager.issue("filesystem.read_file", {"path": "notes.txt"})

    assert manager.consume(
        token, "terminal.run_command", {"path": "notes.txt"}
    ) is False


def test_token_cannot_authorize_changed_arguments():
    manager = ApprovalManager()
    token = manager.issue("filesystem.read_file", {"path": "notes.txt"})

    assert manager.consume(
        token, "filesystem.read_file", {"path": "secrets.txt"}
    ) is False


def test_token_is_single_use():
    manager = ApprovalManager()
    token = manager.issue("example", {"value": 1})

    assert manager.consume(token, "example", {"value": 1}) is True
    assert manager.consume(token, "example", {"value": 1}) is False


def test_expired_token_is_rejected():
    manager = ApprovalManager()
    token = manager.issue("example", {"value": 1}, ttl_seconds=1)

    time.sleep(1.1)

    assert manager.consume(token, "example", {"value": 1}) is False


def test_unknown_token_is_rejected():
    manager = ApprovalManager()

    assert manager.consume(
        "not-a-real-token", "example", {"value": 1}
    ) is False


@pytest.mark.parametrize("ttl", [0, -1, 3601, True, False, 1.5, "60", None])
def test_invalid_expiry_is_rejected(ttl):
    manager = ApprovalManager()

    with pytest.raises(ValueError):
        manager.issue("example", {}, ttl_seconds=ttl)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        None,
        {"bad": object()},
        {"not_finite": math.nan},
        {"not_finite": math.inf},
    ],
)
def test_invalid_arguments_are_rejected_for_issue(arguments):
    manager = ApprovalManager()

    with pytest.raises(ValueError):
        manager.issue("example", arguments)


def test_invalid_arguments_are_rejected_for_consume():
    manager = ApprovalManager()
    token = manager.issue("example", {"value": 1})

    assert manager.consume(token, "example", {"bad": object()}) is False
    # Invalid arguments do not consume the token because no valid fingerprint
    # could be calculated.
    assert manager.consume(token, "example", {"value": 1}) is True


def test_max_pending_must_be_positive_integer():
    for value in (0, -1, True, 1.5, "10"):
        with pytest.raises(ValueError):
            ApprovalManager(max_pending=value)


def test_capacity_limit_rejects_without_evicting_valid_approval():
    manager = ApprovalManager(max_pending=1)
    first = manager.issue("example", {"value": 1})

    with pytest.raises(RuntimeError, match="capacity is full"):
        manager.issue("example", {"value": 2})

    assert manager.consume(first, "example", {"value": 1}) is True


def test_issue_purges_expired_records_before_capacity_check():
    manager = ApprovalManager(max_pending=1)
    token = manager.issue("example", {"value": 1})
    with manager._lock:
        manager._approvals[token] = replace(
            manager._approvals[token],
            expires_at=time.monotonic() - 1,
        )

    replacement_token = manager.issue("example", {"value": 2})
    assert len(manager._approvals) == 1
    assert manager.consume(replacement_token, "example", {"value": 2}) is True


def test_consume_purges_other_expired_records():
    manager = ApprovalManager(max_pending=3)
    expired = manager.issue("example", {"value": 0})
    live = manager.issue("example", {"value": 1})
    with manager._lock:
        manager._approvals[expired] = replace(
            manager._approvals[expired],
            expires_at=time.monotonic() - 1,
        )

    assert manager.consume(live, "example", {"value": 1}) is True
    assert expired not in manager._approvals


def test_concurrent_consumption_succeeds_exactly_once():
    manager = ApprovalManager()
    token = manager.issue("example", {"value": 1})
    workers = 24
    barrier = threading.Barrier(workers)

    def attempt():
        barrier.wait()
        return manager.consume(token, "example", {"value": 1})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: attempt(), range(workers)))

    assert results.count(True) == 1
    assert results.count(False) == workers - 1
