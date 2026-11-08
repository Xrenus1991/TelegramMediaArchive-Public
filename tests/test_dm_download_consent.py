"""Offline check that DM consent is rechecked before and after media download."""
import ast
import unittest
from pathlib import Path


class DmDownloadConsentTests(unittest.TestCase):
    def test_dm_capture_rechecks_active_consent_around_transfer(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        tree = ast.parse(source)
        handler = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "bot_direct_capture"
        )
        checks = []
        downloads = []
        saves = []
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func = node.func
            if isinstance(func.value, ast.Name):
                if func.value.id == "db" and func.attr == "dm_is_active":
                    checks.append(node.lineno)
                elif func.value.id == "message" and func.attr == "download_media":
                    downloads.append(node.lineno)
                elif func.value.id == "db" and func.attr == "dm_save":
                    saves.append(node.lineno)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(len(saves), 1)
        self.assertGreaterEqual(len([line for line in checks if line < downloads[0]]), 2)
        self.assertTrue(any(downloads[0] < line < saves[0] for line in checks))


if __name__ == "__main__":
    unittest.main()
