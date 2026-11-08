import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config
from app.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "media.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_no_chats_monitored_by_default(self):
        self.assertFalse(self.store.is_allowed(-10001))
        self.assertEqual(len(self.store.watched()), 0)

    def test_chats_are_opt_in_and_removable(self):
        self.store.allow(-10001, "A")
        self.assertTrue(self.store.is_allowed(-10001))
        self.store.deny(-10001)
        self.assertFalse(self.store.is_allowed(-10001))

    def test_history_survives_unwatch(self):
        self.store.allow(-10001, "A")
        self.store.save(-10001, 22, 31, "video", "-10001/22.mp4", 33)
        self.store.deny(-10001)
        self.assertTrue(self.store.contains(-10001, 22))
        self.assertEqual(self.store.stats()["total"], 1)

    def test_duplicate_does_not_duplicate_storage_metadata(self):
        for _ in range(3):
            self.store.save(-10001, 1, 31, "photo", "-10001/1.jpg", 10)
        self.assertEqual(self.store.stats()["total"], 1)

    def test_same_message_id_in_different_chats_is_distinct(self):
        self.store.save(-1, 1, None, "photo", "-1/1.jpg", 10)
        self.store.save(-2, 1, None, "photo", "-2/1.jpg", 10)
        self.assertEqual(self.store.stats()["total"], 2)

    def test_delete_only_marks_matching_chat(self):
        self.store.save(-1, 1, None, "photo", "-1/1.jpg", 10)
        self.store.save(-2, 1, None, "photo", "-2/1.jpg", 10)
        self.assertEqual(self.store.marked_deleted(-1, [1]), 1)
        self.assertEqual(self.store.get(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.get(2)["telegram_deleted"], 0)

    def test_ignore_track_reason(self):
        self.store.ignore(-10001, 11, "view-once / self-destructing media")
        self.assertEqual(self.store.ignored_recent()[0]["message_id"], 11)


class ConfigTests(unittest.TestCase):
    def test_requires_credentials(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TELEGRAM_API_ID"):
                load_config()

    def test_owner_private_bot_delivery_disabled_by_default(self):
        from unittest.mock import patch
        secrets = {
            "TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "x",
            "CONTROL_BOT_TOKEN": "token", "OWNER_TELEGRAM_ID": "555",
        }
        with patch.dict(os.environ, secrets, clear=True):
            self.assertFalse(load_config().bot_delivery)


if __name__ == "__main__":
    unittest.main()
