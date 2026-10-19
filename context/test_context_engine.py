"""Focused tests for ContextEngine; run: python -m unittest discover -s context -p "test_context_engine.py" -v"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context.context_engine import ContextEngine

class WorkspaceRepo:
    def __init__(self, root): self.root = root
    def get(self, workspace_id):
        return SimpleNamespace(workspace_id=workspace_id, root_path=self.root)

class Storage:
    def __init__(self, root): self.workspaces = WorkspaceRepo(root)

class ContextEngineTests(unittest.TestCase):
    def test_includes_goal_workspace_and_relevant_text_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("Project overview", encoding="utf-8")
            (root / "planner.py").write_text("def build_plan(): pass", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "secret.py").write_text("must not be read", encoding="utf-8")
            ctx = ContextEngine(Storage(root)).get_relevant_context("ws-1", "build planner plan")
            self.assertEqual(ctx.workspace_id, "ws-1")
            self.assertEqual(ctx.workspace_path, str(root.resolve()))
            self.assertEqual(ctx.conversation_history[0].content, "build planner plan")
            self.assertTrue(any(item.path == "planner.py" for item in ctx.relevant_files))
            self.assertFalse(any("secret.py" in item.path for item in ctx.relevant_files))

    def test_missing_workspace_raises(self):
        class MissingStorage:
            workspaces = SimpleNamespace(get=lambda _id: None)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Workspace not found"):
                ContextEngine(MissingStorage()).get_relevant_context("missing", "goal")

    def test_file_and_character_limits_are_respected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a.py", "b.py", "c.py"):
                (root / name).write_text("x" * 100, encoding="utf-8")
            ctx = ContextEngine(Storage(root), max_files=2, max_total_chars=50).get_relevant_context("ws", "inspect files")
            self.assertLessEqual(len(ctx.relevant_files), 2)
            self.assertLessEqual(sum(len(f.content or "") for f in ctx.relevant_files), 50)

if __name__ == "__main__": unittest.main()
