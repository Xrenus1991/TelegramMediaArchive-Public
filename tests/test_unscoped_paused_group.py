"""Unscoped deletion must honor group pause/leave, even when personally watched."""
import tempfile
import unittest
from pathlib import Path
from app.store import Store


class UnscopedPausedGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db.sqlite3")
        self.chat = -1001
        self.store.allow(self.chat, "watched")
        self.store.register_bot_group(self.chat, "group")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _capture(self, message_id):
        self.assertTrue(self.store.capture_new(self.chat, message_id, 7, "hello", "nonchannel"))

    def _deleted(self, message_id):
        return self.store.db.execute(
            "SELECT telegram_deleted FROM messages WHERE chat_id=? AND message_id=?",
            (self.chat, message_id),
        ).fetchone()[0]

    def test_pending_watched_group_can_mark_deletion(self):
        self._capture(101)
        self.assertEqual(self.store.marked_deleted_unscoped([101]), 1)
        self.assertEqual(self._deleted(101), 1)

    def test_paused_group_is_excluded(self):
        self._capture(102)
        self.store.set_bot_group_state(self.chat, "paused")
        self.assertEqual(self.store.marked_deleted_unscoped([102]), 0)
        self.assertEqual(self._deleted(102), 0)

    def test_left_group_is_excluded(self):
        self._capture(103)
        self.store.set_bot_group_state(self.chat, "left")
        self.assertEqual(self.store.marked_deleted_unscoped([103]), 0)
        self.assertEqual(self._deleted(103), 0)

    def test_paused_candidate_does_not_create_ambiguity(self):
        self._capture(104)
        other = -1002
        self.store.allow(other, "other")
        self.store.capture_new(other, 104, 8, "other", "nonchannel")
        self.store.set_bot_group_state(self.chat, "paused")
        self.assertEqual(self.store.marked_deleted_unscoped([104]), 1)
        self.assertEqual(self._deleted(104), 0)
        self.assertEqual(self.store.db.execute(
            "SELECT telegram_deleted FROM messages WHERE chat_id=? AND message_id=?",
            (other, 104),
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
