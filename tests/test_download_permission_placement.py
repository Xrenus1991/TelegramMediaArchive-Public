"""Guard against starting downloads after consent changes while queued."""
import ast
import unittest
from pathlib import Path


class DownloadPermissionPlacementTests(unittest.TestCase):
    def test_permission_checked_before_download_and_before_save(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        tree = ast.parse(source)
        archive = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "archive_new"
        )
        checks = []
        downloads = []
        saves = []
        for node in ast.walk(archive):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "db" and func.attr == "can_archive":
                    checks.append(node.lineno)
                elif func.value.id == "message" and func.attr == "download_media":
                    downloads.append(node.lineno)
                elif func.value.id == "db" and func.attr == "save":
                    saves.append(node.lineno)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(len(saves), 1)
        self.assertTrue(any(line < downloads[0] for line in checks))
        self.assertTrue(any(downloads[0] < line < saves[0] for line in checks))


if __name__ == "__main__":
    unittest.main()
