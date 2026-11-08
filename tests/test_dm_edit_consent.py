"""Ensure DM edits cannot be archived after a visitor stops consent."""
import ast
import unittest
from pathlib import Path


class DmEditConsentTests(unittest.TestCase):
    def test_dm_edit_handler_checks_active_consent_before_capture(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        tree = ast.parse(source)
        handler = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "bot_direct_edited"
        )
        checks = []
        writes = []
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "db":
                continue
            if node.func.attr == "dm_is_active":
                checks.append(node)
            if node.func.attr == "dm_capture_edit":
                writes.append(node)
        self.assertEqual(len(checks), 1)
        self.assertEqual(len(writes), 1)
        self.assertLess(checks[0].lineno, writes[0].lineno)
        self.assertTrue(any(
            isinstance(node, ast.If) and checks[0] in ast.walk(node.test)
            and writes[0] in ast.walk(node)
            for node in ast.walk(handler)
        ))


if __name__ == "__main__":
    unittest.main()
