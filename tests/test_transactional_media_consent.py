"""Consent regression for downloaded media metadata."""
import tempfile
import unittest
from pathlib import Path

from app.store import Store


class TransactionalMediaConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "archive.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_denied_chat_cannot_save_media(self):
        self.assertFalse(self.store.save_if_allowed(-1, 5, 91, "photo", "-1/5", 10))
        self.assertEqual(len(self.store.latest()), 0)

    def test_allowed_chat_can_save_media_then_revocation_blocks_next(self):
        self.store.allow(-1, "Тест")
        self.assertTrue(self.store.save_if_allowed(-1, 5, 91, "photo", "-1/5", 10))
        self.store.deny(-1)
        self.assertFalse(self.store.save_if_allowed(-1, 6, 91, "photo", "-1/6", 10))
        self.assertEqual(len(self.store.latest()), 1)


if __name__ == "__main__":
    unittest.main()
