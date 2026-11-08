"""Offline integration for opt-in DMs and explicit personal chat selection."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from app.store import Store

class DirectChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'archive.sqlite'
        self.db=Store(self.path)
    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_private_dialog_optin_uses_existing_watched_rules(self):
        self.assertFalse(self.db.is_allowed(5678))
        self.db.allow(5678,'Private friend')
        self.assertTrue(self.db.is_allowed(5678))
        self.db.deny(5678)
        self.assertFalse(self.db.is_allowed(5678))

    def test_guest_not_archived_without_consent(self):
        self.assertFalse(self.db.dm_is_active(300))
        self.assertFalse(self.db.dm_capture_new(300,12,'hi'))
        self.assertFalse(self.db.dm_save(300,12,'photo','bot_dm/300/12.jpg',10))
        self.assertEqual(self.db.dm_count()['texts'],0)
        self.assertEqual(self.db.dm_count()['files'],0)

    def test_consent_enables_text(self):
        self.db.dm_consent(300)
        self.assertTrue(self.db.dm_is_active(300))
        self.assertTrue(self.db.dm_capture_new(300,12,'hello'))
        self.assertEqual(self.db.dm_messages_recent()[0]['text'],'hello')
        self.assertEqual(self.db.dm_count()['people'],1)

    def test_initial_version_does_not_overwrite(self):
        self.db.dm_consent(300)
        self.assertTrue(self.db.dm_capture_new(300,12,'first'))
        self.assertFalse(self.db.dm_capture_new(300,12,'new'))
        self.assertEqual(self.db.dm_get_message(1)['text'],'first')
        self.assertEqual(len(self.db.dm_history(1)),1)

    def test_edits_keep_original_and_latest(self):
        self.db.dm_consent(300);self.db.dm_capture_new(300,12,'first')
        self.assertTrue(self.db.dm_capture_edit(300,12,'second'))
        self.assertEqual([row['text'] for row in self.db.dm_history(1)],['first','second'])
        self.assertEqual(self.db.dm_get_message(1)['text'],'second')

    def test_unknown_edit_not_imported(self):
        self.db.dm_consent(300)
        self.assertFalse(self.db.dm_capture_edit(300,12,'unknown'))

    def test_duplicate_edit_no_new_version(self):
        self.db.dm_consent(300);self.db.dm_capture_new(300,12,'first')
        self.assertFalse(self.db.dm_capture_edit(300,12,'first'))
        self.assertEqual(len(self.db.dm_history(1)),1)

    def test_pause_disables_all_new_media_and_edits(self):
        self.db.dm_consent(300);self.db.dm_capture_new(300,12,'first')
        self.db.dm_pause(300)
        self.assertFalse(self.db.dm_is_active(300))
        self.assertFalse(self.db.dm_capture_new(300,13,'new'))
        self.assertFalse(self.db.dm_capture_edit(300,12,'changed'))
        self.assertFalse(self.db.dm_save(300,15,'video','bot_dm/300/15.mp4',1024))
        self.assertEqual(self.db.dm_get_message(1)['text'],'first')

    def test_reconsent_allows_new_messages(self):
        self.db.dm_consent(300);self.db.dm_pause(300)
        self.db.dm_consent(300)
        self.assertTrue(self.db.dm_capture_new(300,13,'new'))

    def test_bot_and_personal_message_ids_never_collide(self):
        self.db.allow(300,'friend')
        self.db.capture_new(300,12,300,'personal','nonchannel')
        self.db.dm_consent(300)
        self.assertTrue(self.db.dm_capture_new(300,12,'to bot'))
        self.assertEqual(self.db.get_message(1)['text'],'personal')
        self.assertEqual(self.db.dm_get_message(1)['text'],'to bot')

    def test_guest_id_and_message_id_namespace(self):
        self.db.dm_consent(300);self.db.dm_consent(301)
        self.assertTrue(self.db.dm_capture_new(300,12,'Alice'))
        self.assertTrue(self.db.dm_capture_new(301,12,'Bob'))
        self.assertEqual(len(self.db.dm_messages_recent()),2)

    def test_dm_media_and_forget(self):
        self.db.dm_consent(300);self.db.dm_capture_new(300,12,'picture')
        self.assertTrue(self.db.dm_save(300,12,'photo','bot_dm/300/12.jpg',22))
        self.assertFalse(self.db.dm_save(300,12,'photo','bot_dm/300/12.jpg',22))
        self.assertTrue(self.db.dm_contains(300,12))
        self.assertEqual(self.db.dm_get_media(1)['byte_count'],22)
        self.assertEqual(self.db.dm_forget(300),['bot_dm/300/12.jpg'])
        self.assertIsNone(self.db.dm_get_media(1))
        self.assertIsNone(self.db.dm_get_message(1))
        self.assertEqual(len(self.db.dm_people()),0)
        self.assertFalse(self.db.dm_is_active(300))

    def test_forget_only_own_messages(self):
        self.db.dm_consent(300);self.db.dm_consent(301)
        self.db.dm_capture_new(300,10,'A');self.db.dm_capture_new(301,10,'B')
        self.db.dm_save(301,10,'photo','bot_dm/301/10.jpg',22)
        self.assertEqual(self.db.dm_forget(300),[])
        self.assertEqual(self.db.dm_messages_recent()[0]['text'],'B')
        self.assertEqual(self.db.dm_count()['files'],1)

    def test_existing_v3_data_survives_v4_migration(self):
        self.db.allow(-1001,'group');self.db.register_bot_group(-1001,'group')
        self.db.activate_bot_group(-1001)
        self.db.capture_new(-1001,10,5,'important','channel')
        self.db.save(-1001,10,5,'photo','-1001/10.jpg',100)
        self.db.close()
        self.db=Store(self.path)
        self.assertEqual(self.db.get_message(1)['text'],'important')
        self.assertEqual(self.db.get(1)['byte_count'],100)
        self.assertTrue(self.db.bot_group_active(-1001))
        self.assertTrue(self.db.is_allowed(-1001))
        self.assertEqual(self.db.dm_count()['texts'],0)

    def test_all_new_tables_exist(self):
        names={row[0] for row in self.db.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'dm_users','dm_messages','dm_revisions','dm_media'} <= names)

if __name__=='__main__': unittest.main()
