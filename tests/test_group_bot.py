"""Group archiving: prior messages survive migration and pause is respected."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.store import Store
from app.media_rules import kind_of, restricted_reason

class GroupBotStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.folder.name) / 'data.sqlite')

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def test_group_starts_pending_until_disclosed(self):
        self.assertIsNone(self.db.bot_group_state(-10042))
        self.assertEqual(self.db.register_bot_group(-10042, 'Test'), 'pending')
        self.assertFalse(self.db.bot_group_active(-10042))
        self.assertIsNone(self.db.bot_groups()[0]['notice_at'])
        self.db.activate_bot_group(-10042)
        self.assertTrue(self.db.bot_group_active(-10042))
        self.assertIsNotNone(self.db.bot_groups()[0]['notice_at'])

    def test_paused_group_does_not_reactivate_by_registration(self):
        self.db.register_bot_group(-10042, 'First')
        self.db.activate_bot_group(-10042)
        self.db.set_bot_group_state(-10042, 'paused')
        self.assertEqual(self.db.register_bot_group(-10042, 'Updated'), 'paused')
        self.assertFalse(self.db.bot_group_active(-10042))
        self.assertEqual(self.db.bot_groups()[0]['title'], 'Updated')

    def test_left_group_is_not_reenabled_automatically(self):
        self.db.register_bot_group(-10042, 'First')
        self.db.activate_bot_group(-10042)
        self.db.set_bot_group_state(-10042, 'left')
        self.assertEqual(self.db.register_bot_group(-10042, 'Test'), 'left')
        self.assertFalse(self.db.bot_group_active(-10042))

    def test_only_negative_group_ids(self):
        for cid in [0, 123456789]:
            with self.assertRaises(ValueError):
                self.db.register_bot_group(cid, 'Private chats must not auto archive')

    def test_stats_and_invalid_transitions(self):
        self.db.register_bot_group(-1,'First')
        self.db.register_bot_group(-2,'Second')
        self.db.activate_bot_group(-1)
        self.assertEqual(self.db.bot_group_count(), (2, 1))
        self.assertFalse(self.db.set_bot_group_state(-999, 'paused'))
        with self.assertRaises(ValueError):
            self.db.set_bot_group_state(-1, 'evil')

    def test_same_message_from_both_clients_saved_once(self):
        self.db.register_bot_group(-10042,'Test')
        self.db.activate_bot_group(-10042)
        self.assertTrue(self.db.capture_new(-10042,123,15,'hello','channel'))
        self.assertFalse(self.db.capture_new(-10042,123,15,'different','channel'))
        self.assertEqual(self.db.get_message(1)['text'], 'hello')
        self.assertTrue(self.db.capture_edit(-10042,123,'edited'))
        self.assertFalse(self.db.capture_edit(-10042,123,'edited'))
        self.assertEqual(len(self.db.history(1)), 2)

    def test_store_migration_from_v2_keeps_history_and_media(self):
        self.db.capture_new(-1, 5, 12, 'secret', 'channel')
        self.db.save(-1,5,12,'photo','-1/5.jpg',55)
        path=self.db.path
        self.db.close()
        newer=Store(path)
        try:
            self.assertEqual(newer.get_message(1)['text'],'secret')
            self.assertEqual(newer.get(1)['byte_count'],55)
            newer.register_bot_group(-999,'Group')
        finally:
            newer.close()
        self.db=Store(path)
        self.assertEqual(self.db.bot_group_count(), (1,0))

    def test_existing_personal_watch_is_independent(self):
        self.db.allow(-10042,'Personal watch')
        self.db.register_bot_group(-10042,'Bot')
        self.db.set_bot_group_state(-10042,'paused')
        self.assertTrue(self.db.is_allowed(-10042))
        self.assertFalse(self.db.bot_group_active(-10042))

class ExtendedMediaTests(unittest.TestCase):
    def test_document_voice_and_other_types(self):
        defaults=dict(photo=None,video=None,voice=None,video_note=None,
                      sticker=None,gif=None,audio=None,document=None)
        for name, expected in [('voice','voice'),('video_note','video_note'),
                              ('sticker','sticker'),('gif','animation'),('audio','audio')]:
            self.assertEqual(kind_of(SimpleNamespace(**{**defaults,name:True})), expected)
        doc=SimpleNamespace(mime_type='application/pdf')
        self.assertEqual(kind_of(SimpleNamespace(**{**defaults,'document':doc})), 'document')
        self.assertEqual(kind_of(SimpleNamespace(**defaults)), None)

    def test_restricted_media_skipped(self):
        self.assertEqual(restricted_reason(SimpleNamespace(noforwards=True, media=None)),
                         'protected content')
        self.assertIn('self-destruct', restricted_reason(SimpleNamespace(
            noforwards=False, media=SimpleNamespace(ttl_seconds=10))))

if __name__=='__main__':
    unittest.main()
