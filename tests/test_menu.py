"""Offline checks for mobile owner navigation and confirm-before-action UI."""
import unittest
from app import menu

class MobileMenuTests(unittest.TestCase):
    def assert_valid(self, screen):
        self.assertTrue(screen.text)
        self.assertLessEqual(len(screen.text.encode('utf-16-le'))//2, 4096)
        self.assertTrue(screen.rows)
        for group in screen.rows:
            self.assertTrue(group)
            for label, data in group:
                self.assertTrue(label)
                self.assertTrue(data.startswith('ui:'))
                self.assertTrue(1 <= len(data.encode()) <= 64)

    def test_home_contains_main_sections(self):
        screen = menu.main()
        self.assert_valid(screen)
        values = [data for line in screen.rows for _,data in line]
        for key in ('ui:private:1','ui:groups:1','ui:archive','ui:media',
                    'ui:deleted:1','ui:inbox','ui:status','ui:settings','ui:help'):
            self.assertIn(key, values)

    def test_no_password_request(self):
        for screen in (menu.main(),menu.help_screen(),menu.inbox()):
            self.assert_valid(screen)
            self.assertNotIn('API_HASH=',screen.text)

    def test_dialogs_toggle_state_and_ids(self):
        view = menu.dialogs([(5,'Алексей',True),(7,'Ирина',False)], 1, True)
        self.assert_valid(view)
        self.assertEqual(view.rows[0][0][1],'ui:chat:5')
        self.assertEqual(view.rows[1][0][1],'ui:chat:7')
        self.assertIn('✅',view.rows[0][0][0])
        self.assertIn('⏸',view.rows[1][0][0])
        self.assertIn('ui:private:2',[x for r in view.rows for _,x in r])

    def test_dialogs_empty(self):
        self.assert_valid(menu.dialogs([],1,False))

    def test_dialogs_second_page(self):
        view=menu.dialogs([(35,'Тест',False)],2,False)
        self.assertIn('ui:private:1',[x for r in view.rows for _,x in r])
        self.assert_valid(view)

    def test_unsafe_chat_title_clean(self):
        self.assertEqual(menu.clean_title('A\n\x00  B'),'A B')
        self.assertLessEqual(len(menu.clean_title('A'*150)),29)

    def test_private_enable_requires_confirmation(self):
        view=menu.confirm_chat(101,'Ирина',False)
        self.assert_valid(view)
        self.assertIn('собеседника',view.text)
        self.assertIn('ui:chatset:101:1',[data for line in view.rows for _,data in line])

    def test_private_disable_requires_confirmation(self):
        view=menu.confirm_chat(101,'Ирина',True)
        self.assert_valid(view)
        self.assertIn('ui:chatset:101:0',[data for line in view.rows for _,data in line])

    def test_group_on_off_confirm(self):
        active=menu.confirm_group(-100001,'Группа', 'active')
        inactive=menu.confirm_group(-100001,'Группа','paused')
        self.assert_valid(active);self.assert_valid(inactive)
        self.assertIn('ui:groupset:-100001:0',[d for row in active.rows for _,d in row])
        self.assertIn('ui:groupset:-100001:1',[d for row in inactive.rows for _,d in row])
        self.assertIn('уведомление',inactive.text)

    def test_groups_display_status(self):
        view=menu.groups([(-10001,'Чат', 'active'), (-10002,'Другой','paused')],1,False)
        self.assert_valid(view)
        self.assertEqual(view.rows[0][0][1],'ui:group:-10001')

    def test_groups_empty(self):
        self.assert_valid(menu.groups([],1,False))

    def test_archive_and_inbox_navigate(self):
        self.assert_valid(menu.archive())
        self.assert_valid(menu.inbox())

    def test_message_list(self):
        view=menu.list_records('💬 Сообщения',[(12,'#12 Пример')],1,'messages','msg',True,'archive')
        self.assert_valid(view)
        self.assertIn('ui:msg:12',[d for r in view.rows for _,d in r])

    def test_message_list_empty(self):
        self.assert_valid(menu.list_records('📷 Медиа',[],1,'media','fileinfo',False,'home'))

    def test_detail(self):
        self.assert_valid(menu.detail('Подробности',[[menu.row('Назад','ui:home')]],'messages:1'))

    def test_help_notes_limitations(self):
        view=menu.help_screen();self.assert_valid(view)
        self.assertIn('однораз',view.text.lower())

    def test_max_callback_length(self):
        with self.assertRaises(ValueError):
            menu.row('bad','ui:'+'x'*100)

if __name__ == '__main__':
    unittest.main()
