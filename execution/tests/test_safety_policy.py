
from execution.safety_policy import SafetyPolicy


def test_terminal_commands_always_require_approval():
    policy = SafetyPolicy()

    assert policy.requires_approval(
        "terminal.run_command",
        {"command": ["python", "--version"]},
    )


def test_read_only_file_operations_do_not_require_approval():
    policy = SafetyPolicy()

    assert not policy.requires_approval(
        "filesystem.read_file",
        {"path": "notes.txt"},
    )
    assert not policy.requires_approval(
        "filesystem.list_directory",
        {"path": "."},
    )


def test_read_only_git_operations_do_not_require_approval():
    policy = SafetyPolicy()

    assert not policy.requires_approval("git.status", {})
    assert not policy.requires_approval("git.diff", {})
    assert not policy.requires_approval("git.recent_commits", {})


def test_mutating_operations_require_approval():
    policy = SafetyPolicy()

    assert policy.requires_approval("filesystem.write_file", {})
    assert policy.requires_approval("git.commit", {})
    assert policy.requires_approval("filesystem.delete_file", {})


def test_unknown_tools_require_approval():
    policy = SafetyPolicy()

    assert policy.requires_approval("some_new_tool", {})


def test_invalid_inputs_fail_closed():
    policy = SafetyPolicy()

    assert policy.requires_approval("", {})
    assert policy.requires_approval("filesystem.read_file", None)
