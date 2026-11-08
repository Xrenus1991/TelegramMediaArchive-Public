"""Ensure queued events cannot capture message text after archival consent changes."""
import ast
import unittest
from pathlib import Path


class TextCaptureConsentTests(unittest.TestCase):
    def test_archive_new_guards_text_capture(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        tree = ast.parse(source)
        handler = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == "archive_new")
        guards = [node for node in ast.walk(handler)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "db" and node.func.attr == "can_archive"]
        captures = [node for node in ast.walk(handler)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "db" and node.func.attr == "capture_new"]
        self.assertEqual(len(captures), 1)
        self.assertTrue(any(guard.lineno < captures[0].lineno for guard in guards))
        self.assertTrue(any(
            isinstance(node, ast.If) and any(guard in ast.walk(node.test) for guard in guards)
            and any(isinstance(stmt, ast.Return) for stmt in node.body)
            and node.lineno < captures[0].lineno
            for node in ast.walk(handler)
        ))


if __name__ == "__main__":
    unittest.main()
