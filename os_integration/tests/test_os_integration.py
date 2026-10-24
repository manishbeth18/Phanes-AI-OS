
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from os_integration.workspace_detector import WorkspaceDetector
from os_integration.process_manager import ProcessManager
from os_integration.application_manager import ApplicationManager


class TestWorkspaceDetector(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_workspace_with_git_marker(self):
        (self.root / ".git").mkdir()
        detector = WorkspaceDetector()
        result = detector.detect_workspace(self.root)
        self.assertEqual(result, self.root.resolve())

    def test_detect_workspace_returns_none_without_marker(self):
        detector = WorkspaceDetector()
        result = detector.detect_workspace(self.root)
        self.assertIsNone(result)

    def test_validate_existing_directory(self):
        detector = WorkspaceDetector()
        self.assertTrue(detector.validate_workspace(self.root))

    def test_validate_missing_directory(self):
        detector = WorkspaceDetector()
        self.assertFalse(
            detector.validate_workspace(self.root / "missing")
        )


class TestProcessManager(unittest.TestCase):

    def test_rejects_empty_command(self):
        manager = ProcessManager()
        with patch("os.name", "nt"):
            with self.assertRaises(ValueError):
                manager.start_process([], ".")

    def test_rejects_unowned_process(self):
        manager = ProcessManager()
        with self.assertRaises(ValueError):
            manager.terminate_process(999999)

    def test_rejects_command_with_null_character(self):
        manager = ProcessManager()
        with patch("os.name", "nt"):
            with self.assertRaises(ValueError):
                manager.start_process(["invalid\x00command"], ".")

    def test_rejects_command_that_is_not_a_list(self):
        manager = ProcessManager()
        with patch("os.name", "nt"):
            with self.assertRaises(ValueError):
                manager.start_process("not-a-list", ".")

    def test_terminate_waits_and_removes_process(self):
        manager = ProcessManager()
        process = MagicMock()
        process.pid = 123
        process.poll.return_value = None
        manager._owned_processes[123] = process

        result = manager.terminate_process(123, timeout=2)

        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
        self.assertTrue(result)
        self.assertNotIn(123, manager._owned_processes)

    def test_terminate_already_exited_process_removes_tracking(self):
        manager = ProcessManager()
        process = MagicMock()
        process.poll.return_value = 0
        manager._owned_processes[123] = process

        result = manager.terminate_process(123)

        self.assertFalse(result)
        process.terminate.assert_not_called()
        self.assertNotIn(123, manager._owned_processes)

    def test_terminate_timeout_keeps_process_tracked(self):
        manager = ProcessManager()
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("test", 1)
        manager._owned_processes[123] = process

        with self.assertRaises(subprocess.TimeoutExpired):
            manager.terminate_process(123, timeout=1)

        self.assertIn(123, manager._owned_processes)

    def test_shutdown_terminates_all_tracked_processes(self):
        manager = ProcessManager()
        first = MagicMock()
        first.poll.return_value = None
        second = MagicMock()
        second.poll.return_value = None
        manager._owned_processes[123] = first
        manager._owned_processes[456] = second

        manager.shutdown(timeout=2)

        first.terminate.assert_called_once()
        first.wait.assert_called_once_with(timeout=2)
        second.terminate.assert_called_once()
        second.wait.assert_called_once_with(timeout=2)
        self.assertEqual(manager._owned_processes, {})

    def test_rejects_negative_termination_timeout(self):
        manager = ProcessManager()
        with self.assertRaises(ValueError):
            manager.terminate_process(123, timeout=-1)

    def test_rejects_negative_shutdown_timeout(self):
        manager = ProcessManager()
        with self.assertRaises(ValueError):
            manager.shutdown(timeout=-1)


class TestApplicationManager(unittest.TestCase):

    def test_rejects_unapproved_application(self):
        manager = ApplicationManager({})
        with patch("os.name", "nt"):
            with self.assertRaises(ValueError):
                manager.launch("unknown", ".", [])

    def test_rejects_invalid_workspace(self):
        manager = ApplicationManager({"editor": sys.executable})
        with patch("os.name", "nt"):
            with self.assertRaises(ValueError):
                manager.launch(
                    "editor",
                    Path("Z:/this/path/should/not/exist"),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
