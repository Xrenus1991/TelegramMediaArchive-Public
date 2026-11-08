"""Regression tests for ambiguous Telegram deletion events without chat IDs.

No Telegram credentials or network are required.
"""
import tempfile
import unittest
from pathlib import Path

from app.store import Store


class UnscopedDeletionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "archive.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unique_watched_nonchannel_message_is_marked(self):
        self.store.allow(-10, "consented")
        self.store.capture_new(-10, 42, 7, "original", "nonchannel")
        self.store.save(-10, 42, 7, "photo", "-10/42", 100)
        self.assertEqual(self.store.marked_deleted_unscoped([42]), 2)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.get(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.marked_deleted_unscoped([42]), 0)

    def test_same_id_in_two_watched_chats_is_ambiguous(self):
        for chat_id in (-10, -20):
            self.store.allow(chat_id, "consented")
            self.store.capture_new(chat_id, 42, 7, "original", "nonchannel")
        self.assertEqual(self.store.marked_deleted_unscoped([42]), 0)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 0)
        self.assertEqual(self.store.get_message(2)["telegram_deleted"], 0)

    def test_unwatched_message_is_not_marked(self):
        self.store.capture_new(-10, 42, 7, "original", "nonchannel")
        self.assertEqual(self.store.marked_deleted_unscoped([42]), 0)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 0)

    def test_channel_message_is_not_marked_without_chat_id(self):
        self.store.allow(-10, "consented")
        self.store.capture_new(-10, 42, 7, "original", "channel")
        self.assertEqual(self.store.marked_deleted_unscoped([42]), 0)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 0)

    def test_scoped_delete_only_marks_matching_chat(self):
        for chat_id in (-10, -20):
            self.store.capture_new(chat_id, 42, 7, "original", "channel")
        self.assertEqual(self.store.marked_deleted(-10, [42]), 1)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.get_message(2)["telegram_deleted"], 0)


if __name__ == "__main__":
    unittest.main()
