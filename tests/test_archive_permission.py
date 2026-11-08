"""Offline permission regression checks for long-running media transfers."""
import tempfile
import unittest
from pathlib import Path
from app.store import Store


class ArchivePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "archive.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_unwatched_chat_cannot_archive(self):
        self.assertFalse(self.store.can_archive(-101))

    def test_revoked_personal_permission_blocks_archive(self):
        self.store.allow(-101, "watched")
        self.assertTrue(self.store.can_archive(-101))
        self.store.deny(-101)
        self.assertFalse(self.store.can_archive(-101))

    def test_bot_group_requires_activation(self):
        self.store.register_bot_group(-102, "group")
        self.assertFalse(self.store.can_archive(-102))
        self.store.activate_bot_group(-102)
        self.assertTrue(self.store.can_archive(-102))

    def test_pause_overrides_personal_allow(self):
        self.store.allow(-103, "watched")
        self.store.register_bot_group(-103, "group")
        self.assertTrue(self.store.can_archive(-103))
        self.store.set_bot_group_state(-103, "paused")
        self.assertFalse(self.store.can_archive(-103))
        self.store.set_bot_group_state(-103, "left")
        self.assertFalse(self.store.can_archive(-103))


if __name__ == "__main__":
    unittest.main()
