"""Offline tests for conservative backup retention; never delete real data."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.backup_retention import Snapshot, discover, plan, ensure_valid_snapshot


NOW = datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)


def shot(days: int, minutes: int = 0) -> Snapshot:
    stamp = NOW - timedelta(days=days, minutes=minutes)
    return Snapshot(Path("/virtual/backups") / stamp.strftime("%Y%m%dT%H%M%SZ"), stamp)


class RetentionTests(unittest.TestCase):
    def test_ten_same_day_backups_are_all_preserved(self):
        snapshots = [shot(0, i) for i in range(10)]
        kept, removed = plan(snapshots, NOW)
        self.assertEqual(len(kept), 10)
        self.assertEqual(removed, [])

    def test_daily_selection_in_days_seven_to_twentynine(self):
        recent = [shot(0, i) for i in range(5)]
        newest = shot(10, 2)
        older = shot(10, 35)
        kept, removed = plan(recent + [older, newest], NOW)
        self.assertIn(newest, kept)
        self.assertIn(older, removed)

    def test_weekly_selection_in_days_thirty_to_eightynine(self):
        recent = [shot(0, i) for i in range(5)]
        newest = shot(40, 10)
        older = shot(41, 10)
        self.assertEqual(newest.timestamp.isocalendar().week, older.timestamp.isocalendar().week)
        kept, removed = plan(recent + [older, newest], NOW)
        self.assertIn(newest, kept)
        self.assertIn(older, removed)

    def test_over_ninety_days_deleted_only_after_five_newer_backups(self):
        recent = [shot(0, i) for i in range(5)]
        old = shot(120)
        kept, removed = plan(recent + [old], NOW)
        self.assertEqual(len(kept), 5)
        self.assertEqual(removed, [old])

    def test_five_last_backups_always_preserved_even_if_old(self):
        samples = [shot(100 + i) for i in range(5)]
        kept, removed = plan(samples, NOW)
        self.assertEqual(len(kept), 5)
        self.assertEqual(removed, [])

    def test_future_dated_backup_never_pruned(self):
        future = shot(-2)
        kept, removed = plan([future] + [shot(0, i) for i in range(5)], NOW)
        self.assertIn(future, kept)
        self.assertNotIn(future, removed)

    def test_cli_preview_never_deletes_directories(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots = [shot(0, i) for i in range(5)] + [shot(120)]
            for snapshot in snapshots:
                (root / snapshot.path.name).mkdir()
            process = subprocess.run(
                [sys.executable, "scripts/backup_retention.py", "--root", str(root)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("BACKUP_RETENTION_DRY_RUN_ONLY", process.stdout)
            self.assertEqual(sum(1 for p in root.iterdir() if p.is_dir()), 6)

    def test_apply_rejects_nonproduction_root(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for snapshot in [shot(0, i) for i in range(5)] + [shot(120)]:
                (root / snapshot.path.name).mkdir()
            process = subprocess.run(
                [sys.executable, "scripts/backup_retention.py", "--root", str(root), "--apply"],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("APPLY_ALLOWED_ONLY_FOR_DEFAULT_ROOT", process.stderr)
            self.assertEqual(sum(1 for p in root.iterdir() if p.is_dir()), 6)

    def test_symlink_in_root_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / NOW.strftime("%Y%m%dT%H%M%SZ")).symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "SYMLINK"):
                discover(root)

    def test_incomplete_backup_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / NOW.strftime("%Y%m%dT%H%M%SZ")
            folder.mkdir()
            with self.assertRaisesRegex(RuntimeError, "BACKUP_INCOMPLETE"):
                ensure_valid_snapshot(Snapshot(folder, NOW), root)


if __name__ == "__main__":
    unittest.main()
