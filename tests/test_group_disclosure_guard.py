"""Consent/disclosure safety regression tests for bot-managed groups."""
import tempfile
import unittest
from pathlib import Path

from app.store import Store


class GroupDisclosureGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "archive.sqlite")
        self.store.register_bot_group(-1001, "Test group")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_direct_activation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "activate_bot_group"):
            self.store.set_bot_group_state(-1001, "active")
        self.assertEqual(self.store.bot_group_state(-1001), "pending")

    def test_paused_group_cannot_be_directly_reactivated(self):
        self.assertTrue(self.store.set_bot_group_state(-1001, "paused"))
        with self.assertRaises(ValueError):
            self.store.set_bot_group_state(-1001, "active")
        self.assertFalse(self.store.bot_group_active(-1001))

    def test_notice_activation_path_still_works(self):
        self.store.activate_bot_group(-1001)
        self.assertTrue(self.store.bot_group_active(-1001))
        self.assertIsNotNone(self.store.bot_groups()[0]["notice_at"])

    def test_paused_and_left_remain_supported(self):
        self.store.activate_bot_group(-1001)
        self.assertTrue(self.store.set_bot_group_state(-1001, "paused"))
        self.assertFalse(self.store.bot_group_active(-1001))
        self.assertTrue(self.store.set_bot_group_state(-1001, "left"))
        self.assertEqual(self.store.bot_group_state(-1001), "left")


if __name__ == "__main__":
    unittest.main()
