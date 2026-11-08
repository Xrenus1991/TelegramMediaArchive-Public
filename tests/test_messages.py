import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.bot_format import chunks, preview
from app.store import Store


class MessageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "archive.sqlite"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_opt_in_for_chats(self):
        self.assertFalse(self.store.is_allowed(-1))
        self.store.allow(-1, "Тест")
        self.assertTrue(self.store.is_allowed(-1))

    def test_new_message_rejected_after_consent_revoked(self):
        self.store.allow(-1, "test")
        self.assertTrue(self.store.capture_new(-1, 1, 91, "before", "nonchannel"))
        self.store.deny(-1)
        self.assertFalse(self.store.capture_new(-1, 2, 91, "after", "nonchannel"))
        self.assertEqual(self.store.message_stats()["total"], 1)

    def test_new_message_requires_consent(self):
        self.assertFalse(self.store.capture_new(-1, 1, 91, "no consent", "nonchannel"))
        self.assertEqual(self.store.message_stats()["total"], 0)

    def test_new_text_and_idempotency(self):
        self.store.allow(-1, "test")
        self.assertTrue(self.store.capture_new(-1, 5, 91, "Оригинал", "nonchannel"))
        self.assertFalse(self.store.capture_new(-1, 5, 91, "Подмена", "nonchannel"))
        row = self.store.get_message(1)
        self.assertEqual(row["text"], "Оригинал")
        self.assertEqual(row["sender_id"], 91)
        self.assertEqual([x['text'] for x in self.store.history(1)], ["Оригинал"])

    def test_caption_and_empty_text_media(self):
        self.store.allow(-1, "test")
        self.store.allow(-2, "test")
        self.store.capture_new(-2, 6, None, "подпись", "channel")
        self.store.capture_new(-2, 7, None, "", "channel")
        self.assertEqual(self.store.message_stats()["total"], 2)
        self.assertEqual(self.store.message_stats()["texts"], 1)
        self.assertEqual([r["text"] for r in self.store.latest_messages()], ["подпись"])

    def test_edit_rejected_after_consent_revoked(self):
        self.store.allow(-1, "Тест")
        self.store.capture_new(-1, 5, 91, "было", "nonchannel")
        self.assertTrue(self.store.capture_edit(-1, 5, "стало"))
        self.store.deny(-1)
        self.assertFalse(self.store.capture_edit(-1, 5, "не сохранять"))
        self.assertEqual(self.store.get_message(1)["text"], "стало")

    def test_edits_preserve_versions_and_no_duplicates(self):
        self.store.allow(-1, "Тест")
        self.store.capture_new(-1, 5, 91, "было", "nonchannel")
        self.assertTrue(self.store.capture_edit(-1, 5, "стало"))
        self.assertFalse(self.store.capture_edit(-1, 5, "стало"))
        self.assertTrue(self.store.capture_edit(-1, 5, "финал"))
        self.assertEqual([x["text"] for x in self.store.history(1)], ["было", "стало", "финал"])
        self.assertEqual(self.store.get_message(1)["text"], "финал")

    def test_edited_older_message_not_imported(self):
        self.assertFalse(self.store.capture_edit(-1, 5, "нет оригинала"))
        self.assertEqual(self.store.message_stats()["total"], 0)

    def test_download_finishes_after_deletion(self):
        self.store.allow(-1, "test")
        self.store.capture_new(-1, 5, 91, "text", "channel")
        self.store.marked_deleted(-1, [5])
        self.store.save(-1, 5, 91, "photo", "-1/5.jpg", 123)
        self.assertEqual(self.store.get(1)["telegram_deleted"], 1)
        self.assertIsNotNone(self.store.get(1)["deleted_at"])

    def test_known_deletion_marks_text_and_media(self):
        self.store.allow(-1, "test")
        self.store.capture_new(-1, 5, 91, "было", "channel")
        self.store.save(-1, 5, 91, "photo", "-1/5.jpg", 123)
        self.assertEqual(self.store.marked_deleted(-1, [5]), 2)
        self.assertEqual(self.store.marked_deleted(-1, [5]), 0)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.get(1)["telegram_deleted"], 1)
        self.assertEqual(len(self.store.deleted_recent()), 1)
        self.assertEqual(self.store.deleted_recent()[0]["media_id"], 1)
        self.assertFalse(self.store.capture_edit(-1, 5, "после удаления"))

    def test_unknown_chat_deleted_only_unique_nonchannel(self):
        self.store.allow(11, "private")
        self.store.allow(-100, "channel")
        self.store.capture_new(11, 5, 91, "private", "nonchannel")
        self.store.capture_new(-100, 5, 91, "channel", "channel")
        self.assertEqual(self.store.marked_deleted_unscoped([5]), 1)
        self.assertEqual(self.store.get_message(1)["telegram_deleted"], 1)
        self.assertEqual(self.store.get_message(2)["telegram_deleted"], 0)

    def test_unknown_chat_ambiguous_not_marked(self):
        self.store.allow(11, "a")
        self.store.allow(12, "b")
        self.store.capture_new(11, 5, 91, "text", "nonchannel")
        self.store.capture_new(12, 5, 91, "text", "nonchannel")
        self.assertEqual(self.store.marked_deleted_unscoped([5]), 0)
        self.assertEqual(self.store.message_stats()["deleted"], 0)

    def test_unknown_chat_does_not_mark_disabled(self):
        self.store.allow(11, "a")
        self.store.capture_new(11, 5, 91, "text", "nonchannel")
        self.store.deny(11)
        self.assertEqual(self.store.marked_deleted_unscoped([5]), 0)

    def test_old_media_only_deleted_still_listed(self):
        self.store.save(11, 5, None, "photo", "11/5.jpg", 10)
        self.store.marked_deleted(11, [5])
        self.assertEqual(self.store.deleted_recent()[0]["entry_type"], "media")
        self.assertIsNone(self.store.deleted_recent()[0]["archive_id"])

    def test_original_survives_removed_watch(self):
        self.store.allow(-1, "a")
        self.store.capture_new(-1, 5, None, "hello", "channel")
        self.store.deny(-1)
        self.assertEqual(self.store.get_message(1)["text"], "hello")

    def test_separate_chats_same_message_id(self):
        self.store.allow(-1, "test")
        self.store.allow(-2, "test")
        self.store.capture_new(-1, 5, None, "one", "channel")
        self.store.capture_new(-2, 5, None, "two", "channel")
        self.store.marked_deleted(-1, [5])
        self.assertEqual(self.store.get_message(2)["telegram_deleted"], 0)

    def test_pagination_deleted(self):
        self.store.allow(-1, "test")
        for n in range(1, 16):
            self.store.capture_new(-1, n, None, str(n), "channel")
            self.store.marked_deleted(-1, [n])
        self.assertEqual(len(self.store.deleted_recent(10, 1)), 10)
        self.assertEqual(len(self.store.deleted_recent(10, 2)), 5)

    def test_invalid_scope(self):
        with self.assertRaises(ValueError):
            self.store.capture_new(-1, 5, None, "test", "unknown")


class MigrationTests(unittest.TestCase):
    def test_upgrade_v1_schema_no_loss_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.sqlite"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE watched (chat_id INTEGER PRIMARY KEY,title TEXT NOT NULL,added_at TEXT NOT NULL);
                CREATE TABLE media (id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,sender_id INTEGER,kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,byte_count INTEGER NOT NULL,received_at TEXT NOT NULL,
                    telegram_deleted INTEGER NOT NULL DEFAULT 0,UNIQUE(chat_id,message_id));
                CREATE TABLE ignored (chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(chat_id,message_id));
                INSERT INTO watched VALUES (-10,'old','2026-01-01');
                INSERT INTO media(chat_id,message_id,sender_id,kind,relative_path,byte_count,received_at)
                   VALUES(-10,1,4,'photo','-10/1.jpg',42,'2026-01-01');
            """)
            db.close()
            s = Store(path)
            self.assertEqual(s.stats()["total"], 1)
            self.assertEqual(s.watched()[0]["chat_id"], -10)
            s.capture_new(-10, 2, 4, "first text", "channel")
            s.close()
            s = Store(path)
            self.assertEqual(s.stats()["total"], 1)
            self.assertEqual(s.get_message(1)["text"], "first text")
            self.assertIn("deleted_at", [x["name"] for x in s.db.execute("PRAGMA table_info(media)")])
            s.close()


class FormattingTests(unittest.TestCase):
    def test_preview_no_newlines(self):
        self.assertEqual(preview("one\n two"), "one two")
        self.assertEqual(preview("abc", 2), "ab…")

    def test_chunks_no_loss(self):
        text = "hello🙂" * 1000
        result = list(chunks(text, 100))
        self.assertEqual("".join(result), text)
        self.assertLessEqual(max(map(len, result)), 100)

    def test_utf16_emojis_chunk_safely(self):
        chunks_list = list(chunks("😀" * 150, 80))
        self.assertEqual("".join(chunks_list), "😀" * 150)
        self.assertTrue(all(len(s.encode("utf-16-le")) // 2 <= 80 for s in chunks_list))

    def test_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            list(chunks("x", 0))


if __name__ == "__main__":
    unittest.main()
