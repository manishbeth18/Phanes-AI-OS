
import sys
from unittest import result

import pytest

from execution.executor import Executor
from execution.terminal import (
    TerminalToolError,
    TerminalTools,
    register_terminal_tools,
)
from execution.tool_registry import ToolRegistry


@pytest.fixture
def terminal(tmp_path):
    return TerminalTools(tmp_path)


def test_run_python_command(terminal):
    result = terminal.run_command(
        [sys.executable, "-c", "print('PHANES works')"]
    )

    assert result["success"] is True
    assert result["return_code"] == 0
    assert "PHANES works" in result["stdout"]
    assert result["timed_out"] is False


def test_nonzero_exit_code(terminal):
    result = terminal.run_command(
        [sys.executable, "-c", "raise SystemExit(3)"]
    )

    assert result["success"] is False
    assert result["return_code"] == 3


def test_invalid_empty_command(terminal):
    with pytest.raises(TerminalToolError):
        terminal.run_command([])


def test_invalid_timeout(terminal):
    with pytest.raises(TerminalToolError):
        terminal.run_command(
            [sys.executable, "-c", "print('hello')"],
            timeout_seconds=0,
        )


def test_reject_absolute_working_directory(terminal, tmp_path):
    with pytest.raises(TerminalToolError):
        terminal.run_command(
            [sys.executable, "-c", "print('hello')"],
            cwd=str(tmp_path),
        )


def test_reject_path_traversal(terminal):
    with pytest.raises(TerminalToolError):
        terminal.run_command(
            [sys.executable, "-c", "print('hello')"],
            cwd="..",
        )


def test_output_is_truncated(terminal):
    result = terminal.run_command(
        [
            sys.executable,
            "-c",
            "print('x' * 500)",
        ],
        max_output_chars=20,
    )

    assert result["success"] is True
    assert len(result["stdout"]) == 20


def test_timeout_is_reported(terminal):
    result = terminal.run_command(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(3)",
        ],
        timeout_seconds=1,
    )

    assert result["success"] is False
    assert result["timed_out"] is True
    assert result["return_code"] is None



def test_terminal_registered_and_executed(tmp_path):
    registry = ToolRegistry()
    register_terminal_tools(registry, tmp_path)
    executor = Executor(registry)

    arguments = {
        "command": [
            sys.executable,
            "-c",
            "print('registered tool')",
        ]
    }
    token = executor.approval_manager.issue(
        "terminal.run_command",
        arguments,
    )

    result = executor.execute(
        "terminal.run_command",
        arguments,
        approval_token=token,
    )

    assert result.success is True
    assert "registered tool" in str(result.output)
