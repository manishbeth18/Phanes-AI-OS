
import time

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


def test_invalid_expiry_is_rejected():
    manager = ApprovalManager()

    try:
        manager.issue("example", {}, ttl_seconds=0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid expiry to be rejected.")
