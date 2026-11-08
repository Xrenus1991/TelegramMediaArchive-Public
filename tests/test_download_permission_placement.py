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
                elif func.value.id == "db" and func.attr == "save_if_allowed":
                    saves.append(node.lineno)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(len(saves), 1)
        self.assertTrue(any(line < downloads[0] for line in checks))
        self.assertTrue(any(downloads[0] < line < saves[0] for line in checks))
        # The final write must also be guarded within Store's transaction.
        store_tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "store.py").read_text())
        guarded = next(n for n in ast.walk(store_tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "save_if_allowed")
        self.assertTrue(any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "can_archive" for n in ast.walk(guarded)
        ))


if __name__ == "__main__":
    unittest.main()
