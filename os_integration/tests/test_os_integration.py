import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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