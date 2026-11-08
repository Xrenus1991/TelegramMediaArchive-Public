"""Guard cleanup of incomplete media downloads without deleting existing files."""
import ast
import unittest
from pathlib import Path


class PartialDownloadCleanupTests(unittest.TestCase):
    def test_cleanup_on_failure_preserves_existing_targets(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text())
        handler = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.AsyncFunctionDef) and n.name == "archive_new")
        # Both the missing-download and exception paths must check whether
        # the target existed before the attempt and reject symlink cleanup.
        checks = [n for n in ast.walk(handler) if isinstance(n, ast.If)
                  and "not target_existed" in ast.unparse(n.test)
                  and "not target.is_symlink()" in ast.unparse(n.test)]
        self.assertGreaterEqual(len(checks), 2)
        self.assertTrue(all(any(isinstance(x, ast.Call)
                                and isinstance(x.func, ast.Attribute)
                                and x.func.attr == "unlink"
                                for x in ast.walk(n)) for n in checks))
        self.assertTrue(any(isinstance(n, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "target_existed"
                                    for t in n.targets)
                            for n in ast.walk(handler)))


if __name__ == "__main__":
    unittest.main()
