import os
import sys
import threading
import time

import pytest

from execution.terminal import TerminalTools


@pytest.fixture
def terminal(tmp_path):
    return TerminalTools(tmp_path)


def test_large_stdout_is_bounded(terminal):
    result = terminal.run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 2_000_000)",
        ],
        timeout_seconds=10,
        max_output_chars=100,
    )

    assert result["success"] is True
    assert len(result["stdout"]) == 100
    assert "output truncated" in result["stdout"]


def test_large_stderr_is_bounded(terminal):
    result = terminal.run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('e' * 2_000_000)",
        ],
        timeout_seconds=10,
        max_output_chars=100,
    )

    assert result["success"] is True
    assert len(result["stderr"]) == 100
    assert "output truncated" in result["stderr"]


def test_cancel_event_cancels_running_command(terminal):
    cancel_event = threading.Event()
    result_holder = {}

    def run():
        result_holder["result"] = terminal.run_command(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(20)",
            ],
            timeout_seconds=10,
            cancel_event=cancel_event,
        )

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.2)
    cancel_event.set()
    worker.join(timeout=5)

    assert not worker.is_alive(), "Cancelled command did not return promptly"
    result = result_holder["result"]
    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["timed_out"] is False
    assert result["return_code"] is None


def test_pre_set_cancel_event_cancels_command(terminal):
    cancel_event = threading.Event()
    cancel_event.set()

    result = terminal.run_command(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
        timeout_seconds=10,
        cancel_event=cancel_event,
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["return_code"] is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group behavior")
def test_timeout_terminates_process_group(terminal, tmp_path):
    marker = tmp_path / "child-survived.txt"
    child_code = (
        f"import time; time.sleep(2); "
        f"open({str(marker)!r}, 'w').write('alive')"
    )
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )

    result = terminal.run_command(
        [sys.executable, "-c", code],
        timeout_seconds=1,
        max_output_chars=100,
    )

    assert result["timed_out"] is True
    time.sleep(2.5)
    assert not marker.exists()
