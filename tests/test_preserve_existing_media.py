"""Regression: revoked consent must not delete files predating a retry."""
import ast
import unittest
from pathlib import Path


class PreserveExistingArchiveTests(unittest.TestCase):
    def test_revocation_cleanup_checks_target_existed(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text())
        handler = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.AsyncFunctionDef) and n.name == "archive_new")
        guards = [n for n in ast.walk(handler)
                  if isinstance(n, ast.If)
                  and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                          and isinstance(c.func.value, ast.Name)
                          and c.func.value.id == "db" and c.func.attr == "can_archive"
                          for c in ast.walk(n.test))]
        cleanup = [n for n in guards if any(
            isinstance(child, ast.If)
            and any(isinstance(c, ast.Name) and c.id == "target_existed"
                    for c in ast.walk(child.test))
            and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "unlink" for c in ast.walk(child))
            for child in n.body)]
        self.assertTrue(cleanup, "revocation cleanup must preserve preexisting targets")


    def test_existing_target_returns_before_download(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text())
        handler = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.AsyncFunctionDef) and n.name == "archive_new")
        existing = [n for n in ast.walk(handler)
                    if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                    and n.test.id == "target_existed"
                    and any(isinstance(child, ast.Return) for child in n.body)]
        downloads = [n for n in ast.walk(handler)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "download_media"]
        self.assertEqual(len(downloads), 1)
        self.assertTrue(any(n.lineno < downloads[0].lineno for n in existing))


if __name__ == "__main__":
    unittest.main()
