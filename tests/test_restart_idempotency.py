"""Offline SQLite restart and replay regression tests."""
import tempfile
import unittest
from pathlib import Path
from app.store import Store


class RestartIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "archive.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_replayed_message_after_restart_preserves_original(self):
        store = Store(self.path)
        try:
            self.assertTrue(store.capture_new(-10, 101, 7, "original", "nonchannel"))
        finally:
            store.close()
        store = Store(self.path)
        try:
            self.assertFalse(store.capture_new(-10, 101, 7, "replayed", "nonchannel"))
            self.assertEqual(store.get_message(1)["text"], "original")
            self.assertEqual(len(store.history(1)), 1)
        finally:
            store.close()

    def test_media_metadata_deduplicated_across_restart(self):
        store = Store(self.path)
        try:
            store.save(-10, 101, 7, "photo", "-10/101.jpg", 100)
        finally:
            store.close()
        store = Store(self.path)
        try:
            store.save(-10, 101, 7, "photo", "-10/101.jpg", 100)
            self.assertEqual(store.stats()["total"], 1)
            self.assertEqual(store.get(1)["relative_path"], "-10/101.jpg")
        finally:
            store.close()

    def test_deletion_survives_restart_and_late_media(self):
        store = Store(self.path)
        try:
            store.capture_new(-10, 101, 7, "original", "channel")
            self.assertEqual(store.marked_deleted(-10, [101]), 1)
        finally:
            store.close()
        store = Store(self.path)
        try:
            store.save(-10, 101, 7, "photo", "-10/101.jpg", 100)
            self.assertEqual(store.get(1)["telegram_deleted"], 1)
            self.assertEqual(store.get_message(1)["telegram_deleted"], 1)
            self.assertEqual(store.marked_deleted(-10, [101]), 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
