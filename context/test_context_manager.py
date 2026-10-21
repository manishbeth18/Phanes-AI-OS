"""Unit tests for ContextManager."""
import unittest
from context.context_manager import ContextManager

class ContextManagerTests(unittest.TestCase):
    def test_add_and_get_context(self):
        manager = ContextManager()
        manager.add("  keep this  ", "user")
        items = manager.get_context()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].content, "keep this")
        self.assertEqual(items[0].source, "user")

    def test_ignores_empty_content(self):
        manager = ContextManager()
        manager.add("  \n", "user")
        self.assertEqual(manager.get_context(), [])

    def test_clear(self):
        manager = ContextManager()
        manager.add("some context", "test")
        manager.clear()
        self.assertEqual(manager.get_context(), [])

if __name__ == "__main__":
    unittest.main()
