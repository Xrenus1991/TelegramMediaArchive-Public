"""Regression: edited messages require active archive consent at the storage layer."""
import ast
import unittest
from pathlib import Path


class TransactionalEditConsentTests(unittest.TestCase):
    def test_capture_edit_checks_consent_after_transaction_begins(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "store.py").read_text())
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "capture_edit")
        begin = [n for n in ast.walk(method)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "execute" and n.args
                 and isinstance(n.args[0], ast.Constant) and n.args[0].value == "BEGIN IMMEDIATE"]
        checks = [n for n in ast.walk(method)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "can_archive"]
        writes = [n for n in ast.walk(method)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "execute" and n.args
                  and isinstance(n.args[0], ast.Constant)
                  and "UPDATE messages SET text=" in n.args[0].value]
        self.assertEqual(len(begin), 1)
        self.assertEqual(len(checks), 1)
        self.assertEqual(len(writes), 1)
        self.assertLess(begin[0].lineno, checks[0].lineno)
        self.assertLess(checks[0].lineno, writes[0].lineno)
        self.assertTrue(any(
            isinstance(n, ast.If) and checks[0] in ast.walk(n.test)
            and any(isinstance(x, ast.Return) for x in n.body)
            for n in ast.walk(method)
        ))


if __name__ == "__main__":
    unittest.main()
