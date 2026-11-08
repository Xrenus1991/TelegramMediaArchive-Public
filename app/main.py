"""Telegram Media Archive v5: accessible owner menu, v4 archival preserved."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

from .config import load_config
from .store import Store
from .media_rules import kind_of, restricted_reason, safe_archive_path
from .bot_format import preview, chunks
from . import menu

LOG = logging.getLogger("tg-media-archive")


async def run():
    cfg = load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    media_root = cfg.data_dir / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.data_dir, 0o700)
    db = Store(cfg.data_dir / "archive.sqlite")

    # Different session names and client objects; never share with other projects.
    personal = TelegramClient(str(cfg.data_dir / "user"), cfg.api_id, cfg.api_hash)
    controller = TelegramClient(StringSession(), cfg.api_id, cfg.api_hash)
    await personal.connect()
    if not await personal.is_user_authorized():
        raise RuntimeError("No authorized user session. Run python -m app.login first.")
    me = await personal.get_me()
    if cfg.owner_id and cfg.owner_id != me.id:
        raise RuntimeError("OWNER_TELEGRAM_ID does not match the authenticated personal account")
    owner_id = me.id
    await controller.start(bot_token=cfg.bot_token)
    semaphore = asyncio.Semaphore(2)
    file_limit = cfg.max_file_mb * 1024 * 1024
    pending_downloads: set[tuple[int, int]] = set()
    group_locks: dict[int, asyncio.Lock] = {}
    bot_me = await controller.get_me()
    GROUP_NOTICE = (
        "📦 В этой группе включено архивирование сообщений.\n"
        "Бот сохраняет новые сообщения, тексты, правки и доступные вложения "
        "на отдельном сервере. Копии могут сохраняться после удаления "
        "исходного сообщения и доступны владельцу архива.\n"
        "Одноразовые и защищённые медиа не сохраняются.\n"
        "Администратор группы может остановить запись: /archive_off. "
        "Проверить состояние: /archive_status.\n"
        "Используйте бота только с ведома участников."
    )

    async def archive_new(event):
        """Capture new messages from either client; deduplicate across both."""
        chat_id = event.chat_id
        message = event.message
        if chat_id is None or message is None:
            return
        kind = kind_of(message)
        reason = restricted_reason(message)
        if reason:
            db.ignore(chat_id, message.id, reason)
            return
        raw_text = message.raw_text or ""
        if kind or raw_text:
            if db.capture_new(chat_id, message.id, message.sender_id, raw_text,
                              "channel" if event.is_channel else "nonchannel"):
                LOG.info("Message captured: chat=%s message=%s text_chars=%s",
                         chat_id, message.id, len(raw_text))
        if not kind or db.contains(chat_id, message.id):
            return
        token = (chat_id, message.id)
        if token in pending_downloads:
            return
        pending_downloads.add(token)
        try:
            size = getattr(getattr(message, "file", None), "size", None)
            if size is not None and size > file_limit:
                db.ignore(chat_id, message.id, "file exceeds configured limit")
                return
            async with semaphore:
                # The transfer may have waited in the semaphore queue after
                # consent was revoked. Do not start a new download in that case.
                if not db.can_archive(chat_id):
                    LOG.info("Skipped media download after archival permission changed: chat=%s", chat_id)
                    return
                if shutil.disk_usage(cfg.data_dir).free < cfg.min_free_mb * 1024 * 1024:
                    db.ignore(chat_id, message.id, "insufficient free disk space")
                    return
                folder = media_root / str(chat_id)
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / str(message.id)
                try:
                    downloaded = await asyncio.wait_for(
                        message.download_media(file=str(target)), timeout=900
                    )
                    if not downloaded:
                        db.ignore(chat_id, message.id, "download unavailable")
                        return
                    path = Path(downloaded).resolve()
                    if not path.is_relative_to(media_root.resolve()):
                        LOG.error("Unsafe media path rejected")
                        return
                    actual_size = path.stat().st_size
                    if actual_size > file_limit:
                        path.unlink(missing_ok=True)
                        db.ignore(chat_id, message.id, "download exceeds file limit")
                        return
                    # Consent may have been withdrawn while Telegram was downloading.
                    if not db.can_archive(chat_id):
                        path.unlink(missing_ok=True)
                        LOG.info("Discarded downloaded media after archival permission changed: chat=%s", chat_id)
                        return
                    db.save(chat_id, message.id, message.sender_id, kind,
                            str(path.relative_to(media_root)), actual_size)
                    LOG.info("Saved %s: chat=%s message=%s bytes=%s",
                             kind, chat_id, message.id, actual_size)
                except Exception:
                    LOG.exception("Media download failed: chat=%s message=%s", chat_id, message.id)
        finally:
            pending_downloads.discard(token)

    @personal.on(events.NewMessage())
    async def handle_message(event):
        if (event.chat_id is not None and db.is_allowed(event.chat_id)
            and db.bot_group_state(event.chat_id) not in ("paused", "left")):
            await archive_new(event)

    @personal.on(events.MessageEdited())
    async def handle_edit(event):
        chat_id = event.chat_id
        if (chat_id is None or
            not (db.is_allowed(chat_id) or db.bot_group_active(chat_id)) or
            db.bot_group_state(chat_id) in ("paused", "left") or
            restricted_reason(event.message)):
            return
        # Never add previously unseen messages merely because they were edited.
        if db.capture_edit(chat_id, event.message.id, event.raw_text or ""):
            LOG.info("Message edited: chat=%s message=%s", chat_id, event.message.id)

    @personal.on(events.MessageDeleted())
    async def handle_delete(event):
        if event.chat_id is not None:
            if (not (db.is_allowed(event.chat_id) or db.bot_group_active(event.chat_id))
                or db.bot_group_state(event.chat_id) in ("paused", "left")):
                return
            count = db.marked_deleted(event.chat_id, event.deleted_ids)
        else:
            # Nonchannel Telegram IDs are globally unique, but only use a match
            # to a saved, known nonchannel message in exactly one watched chat.
            count = db.marked_deleted_unscoped(event.deleted_ids)
        if count:
            LOG.info("Confirmed deletion: chat=%s affected_records=%s", event.chat_id, count)

    async def group_title(event):
        try:
            chat = await event.get_chat()
            return " ".join((getattr(chat, "title", None) or str(event.chat_id)).split())[:200]
        except Exception:
            return str(event.chat_id)

    async def announce_group(event, *, rejoined=False) -> bool:
        """Activate only AFTER visible notice has been successfully sent."""
        if not cfg.auto_group_archive or not event.is_group or event.chat_id is None:
            return False
        chat_id = event.chat_id
        lock = group_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            state = db.bot_group_state(chat_id)
            if state is None:
                state = db.register_bot_group(chat_id, await group_title(event))
            if state == "left" and rejoined:
                db.set_bot_group_state(chat_id, "pending")
                state = "pending"
            if state == "active":
                return True
            if state != "pending":
                return False  # Admin paused archival: never silently resume.
            try:
                await controller.send_message(chat_id, GROUP_NOTICE, parse_mode=None)
            except Exception:
                LOG.exception("Archive notice failed; group remains disabled: %s", chat_id)
                return False
            db.activate_bot_group(chat_id)
            LOG.info("Bot group activated after disclosure: %s", chat_id)
            return False  # Never archive the trigger message before disclosure.

    async def is_group_admin(event) -> bool:
        if event.sender_id == owner_id:
            return True
        if event.sender_id is None:
            return False
        try:
            rights = await controller.get_permissions(event.chat_id, event.sender_id)
            return bool(getattr(rights, "is_admin", False) or
                        getattr(rights, "is_creator", False))
        except Exception:
            return False

    DM_NOTICE = (
        "🔐 Это личный чат с ботом Telegram Media Archive.\n"
        "Если вы согласитесь, бот будет сохранять ваши следующие сообщения, "
        "тексты, правки и доступные фото/видео/файлы на отдельном сервере; "
        "архив доступен владельцу бота и может сохраняться после удаления в Telegram.\n"
        "Без согласия сообщения не архивируются.\n"
        "Отправьте /agree для начала, /stop для паузы или /forget для удаления "
        "вашего архива. /privacy — повторить условия.\n"
        "Не отправляйте одноразовые или защищённые материалы."
    )
    dm_pending: set[tuple[int,int]] = set()

    def visitor_buttons(person):
        if db.dm_is_active(person):
            return [[Button.inline('⏸ Остановить запись', b'dm:stop')],
                    [Button.inline('🗑 Удалить свой архив', b'dm:forgetask')],
                    [Button.inline('🔐 Условия хранения', b'dm:privacy')]]
        return [[Button.inline('✅ Согласен на сохранение', b'dm:agree')],
                [Button.inline('🔐 Подробнее', b'dm:privacy')]]

    async def visitor_reply(event, person, message=DM_NOTICE):
        await event.reply(message, buttons=visitor_buttons(person), parse_mode=None)


    async def bot_direct_capture(event):
        """Bot DM data uses separate DB tables and folders, never personal account keys."""
        user_id = event.sender_id
        message = event.message
        if user_id is None or not db.dm_is_active(user_id):
            return
        reason = restricted_reason(message)
        if reason:
            return
        kind = kind_of(message)
        text = message.raw_text or ""
        if kind or text:
            db.dm_capture_new(user_id, message.id, text)
        if not kind or db.dm_contains(user_id, message.id):
            return
        key = (user_id,message.id)
        if key in dm_pending:
            return
        dm_pending.add(key)
        try:
            size = getattr(getattr(message,'file',None),'size',None)
            if size is not None and size > file_limit:
                return
            async with semaphore:
                # The visitor may have pressed /stop or /forget while this
                # download waited for a semaphore slot.
                if not db.dm_is_active(user_id):
                    return
                if shutil.disk_usage(cfg.data_dir).free < cfg.min_free_mb * 1024 * 1024:
                    return
                folder = media_root / 'bot_dm' / str(user_id)
                folder.mkdir(parents=True, exist_ok=True)
                downloaded = await asyncio.wait_for(
                    message.download_media(file=str(folder / str(message.id))), timeout=900)
                if not downloaded:
                    return
                path = Path(downloaded).resolve()
                if not path.is_relative_to(media_root.resolve()):
                    LOG.error('Unsafe bot DM media path')
                    return
                if path.stat().st_size > file_limit or not db.dm_is_active(user_id):
                    path.unlink(missing_ok=True)
                    return
                relative = str(path.relative_to(media_root))
                if not db.dm_save(user_id,message.id,kind,relative,path.stat().st_size):
                    path.unlink(missing_ok=True)
        except Exception:
            LOG.exception('Bot DM media download failed for user=%s',user_id)
        finally:
            dm_pending.discard(key)

    @controller.on(events.MessageEdited())
    async def bot_direct_edited(event):
        if event.is_private and event.sender_id != owner_id and event.sender_id is not None:
            if not restricted_reason(event.message):
                db.dm_capture_edit(event.sender_id,event.message.id,event.raw_text or "")

    @controller.on(events.ChatAction())
    async def bot_joined_or_left(event):
        if not cfg.auto_group_archive or not event.is_group:
            return
        joined_user_ids = getattr(event, "user_ids", None) or []
        if bot_me.id not in joined_user_ids:
            return
        if getattr(event, "user_added", False) or getattr(event, "user_joined", False):
            await announce_group(event, rejoined=True)
        elif getattr(event, "user_kicked", False) or getattr(event, "user_left", False):
            db.set_bot_group_state(event.chat_id, "left")

    @controller.on(events.MessageEdited())
    async def bot_group_edited(event):
        if (cfg.auto_group_archive and event.is_group and event.chat_id is not None
            and db.bot_group_active(event.chat_id) and
            not restricted_reason(event.message)):
            db.capture_edit(event.chat_id, event.message.id, event.raw_text or "")

    async def handle_bot_group_message(event):
        if not cfg.auto_group_archive or event.chat_id is None:
            return
        raw = (event.raw_text or "").strip()
        command = raw.split(None, 1)[0].split("@", 1)[0].lower() if raw else ""
        if command in ("/archive_off", "/archive_on", "/archive_status", "/archive_privacy"):
            if command == "/archive_privacy":
                await event.reply(GROUP_NOTICE, parse_mode=None)
                return
            if command == "/archive_status":
                status = db.bot_group_state(event.chat_id) or "не настроено"
                await event.reply(f"📦 Состояние архива этой группы: {status}", parse_mode=None)
                return
            if not await is_group_admin(event):
                await event.reply("Только администратор группы может менять архивирование.", parse_mode=None)
                return
            if command == "/archive_off":
                if db.bot_group_state(event.chat_id) is None:
                    db.register_bot_group(event.chat_id, await group_title(event))
                db.set_bot_group_state(event.chat_id, "paused")
                await event.reply("⏸ Архивирование новых сообщений этой группы остановлено. "
                                  "Уже сохранённые файлы не удалены.", parse_mode=None)
                return
            # /archive_on: disclose again before storing anything.
            if db.bot_group_state(event.chat_id) is None:
                db.register_bot_group(event.chat_id, await group_title(event))
            db.set_bot_group_state(event.chat_id, "pending")
            await announce_group(event)
            if not db.bot_group_active(event.chat_id):
                await event.reply("Не удалось включить архив: сообщение-уведомление не отправлено.",
                                  parse_mode=None)
            return
        # This first message activates the group only after announcing it.
        previously_active = db.bot_group_active(event.chat_id)
        if not previously_active:
            await announce_group(event)
            return
        await archive_new(event)

    # Mobile-first owner interface. Callback arguments are untrusted: validate
    # ownership, chat type, row existence and current state before every action.
    def ui_buttons(rows):
        return [[Button.inline(label, data.encode('utf-8')) for label, data in line]
                for line in rows]

    async def ui_screen(event, route='home', *, edit=False):
        parts = route.split(':')
        section = parts[0] if parts else ''
        if not parts or len(route.encode('utf-8')) > 64:
            raise ValueError('Invalid menu request')
        def parse_int(value, *, allow_negative=False):
            number = int(value)
            if abs(number) > 10**19 or (not allow_negative and number < 1):
                raise ValueError('Invalid number')
            return number
        def page_arg():
            page = parse_int(parts[1]) if len(parts) == 2 else 1
            if page > 100:
                raise ValueError('Page out of range')
            return page
        async def paginate_private(page):
            users = []
            async for d in personal.iter_dialogs():
                if (getattr(d, 'is_user', False) and
                    not getattr(d.entity, 'bot', False) and d.id != owner_id):
                    users.append(d)
                if len(users) >= page * 7 + 1:
                    break
            page_rows = users[(page-1)*7:page*7]
            return menu.dialogs([(d.id, d.name, db.is_allowed(d.id)) for d in page_rows],
                                page, len(users) > page*7)
        async def find_private(chat_id):
            async for d in personal.iter_dialogs():
                if (d.id == chat_id and getattr(d, 'is_user', False)
                    and not getattr(d.entity,'bot',False) and d.id != owner_id):
                    return d
            return None
        if section == 'home':
            screen = menu.main()
        elif section == 'help':
            screen = menu.help_screen()
        elif section == 'archive':
            screen = menu.archive()
        elif section == 'inbox':
            screen = menu.inbox()
        elif section in ('status', 'settings'):
            st = db.stats()
            ms = db.message_stats()
            n_group, active = db.bot_group_count()
            dm = db.dm_count()
            enabled = len(db.watched())
            info = (f"📊 СОСТОЯНИЕ АРХИВА\n\n"
                    f"💬 Сообщений: {ms['total']}\n"
                    f"📷 Медиа: {st['total']} ({st['size']/1048576:.1f} МБ)\n"
                    f"🗑 Подтверждённых удалений: {ms['deleted'] or 0}\n"
                    f"👁 Выбрано чатов аккаунта: {enabled}\n"
                    f"👥 Группы с ботом: {active} из {n_group} активны\n"
                    f"✉️ Написали боту: {dm['texts']} текстов, {dm['files']} файлов\n"
                    f"💾 Свободно: {shutil.disk_usage(cfg.data_dir).free/1073741824:.1f} ГБ\n"
                    f"📲 Выдача файлов: {'включена' if cfg.bot_delivery else 'отключена'}")
            if section == 'settings':
                info = ("⚙️ НАСТРОЙКИ\n\n" + info +
                        "\n\nПараметры сервера и токены не изменяются через бота. "
                        "Включение архива для каждого чата — в разделе «Личные чаты»." )
            screen = menu.Screen(info, [[menu.row('🔄 Обновить', 'ui:'+section)], menu.back()])
        elif section == 'private':
            screen = await paginate_private(page_arg())
        elif section == 'chat':
            if len(parts) != 2:
                raise ValueError('Missing ID')
            chat_id = parse_int(parts[1])
            d = await find_private(chat_id)
            screen = (menu.confirm_chat(chat_id, d.name, db.is_allowed(chat_id))
                      if d else menu.detail('Чат недоступен в личном аккаунте.', [], 'private:1'))
        elif section == 'chatset':
            if len(parts) != 3 or parts[2] not in ('0','1'):
                raise ValueError('Invalid action')
            chat_id = parse_int(parts[1])
            d = await find_private(chat_id)
            if d is None:
                screen = menu.detail('Чат больше недоступен.', [], 'private:1')
            else:
                enabled = parts[2] == '1'
                if enabled:
                    db.allow(chat_id, ' '.join(d.name.split()))
                else:
                    db.deny(chat_id)
                screen = menu.detail(
                    ('✅ Сохранение включено: ' if enabled else '⏸ Сохранение остановлено: ')
                    + menu.clean_title(d.name, 90)
                    + ('\nСохраняются только новые сообщения. Проинформируйте собеседника.'
                       if enabled else '\nСтарые записи сохранены.'),
                    [[menu.row('💬 Другие чаты', 'ui:private:1')]], 'home')
        elif section == 'groups':
            page = page_arg()
            all_rows = db.bot_groups()
            group_rows = all_rows[(page-1)*7:page*7]
            screen = menu.groups([(r['chat_id'],r['title'],r['state']) for r in group_rows],
                                 page, len(all_rows) > page*7)
        elif section == 'group':
            if len(parts)!=2:
                raise ValueError('Missing group')
            gid = parse_int(parts[1], allow_negative=True)
            found = next((r for r in db.bot_groups() if r['chat_id'] == gid), None)
            screen = (menu.confirm_group(gid,found['title'],found['state']) if found and
                      found['state'] != 'left' else
                      menu.detail('Бот отсутствует в этой группе.', [], 'groups:1'))
        elif section == 'groupset':
            if len(parts)!=3 or parts[2] not in ('0','1'):
                raise ValueError('Invalid action')
            gid = parse_int(parts[1], allow_negative=True)
            state = db.bot_group_state(gid)
            if state is None or state == 'left':
                screen = menu.detail('Эта группа недоступна.', [], 'groups:1')
            elif parts[2] == '0':
                db.set_bot_group_state(gid, 'paused')
                try:
                    await controller.send_message(
                        gid, '⏸ Архивирование новых сообщений остановлено владельцем бота.',
                        parse_mode=None)
                except Exception:
                    LOG.warning('Group pause notification could not be delivered: %s', gid)
                screen = menu.detail('⏸ Сохранение в группе остановлено. Старые записи остались.',
                                     [], 'groups:1')
            else:
                db.set_bot_group_state(gid, 'pending')
                class GroupRef:
                    chat_id = gid
                    is_group = True
                    async def get_chat(self):
                        return await controller.get_entity(gid)
                await announce_group(GroupRef())
                if db.bot_group_active(gid):
                    info='✅ Архивирование группы включено после публичного уведомления.'
                else:
                    info='⚠️ Не удалось уведомить группу — архивирование не включено.'
                screen = menu.detail(info, [], 'groups:1')
        elif section in ('messages','media','inboxmessages','inboxmedia'):
            page = page_arg()
            limit = 7
            off = (page-1)*limit
            if section == 'messages':
                rows = db.db.execute('SELECT id,chat_id,text,edited_at FROM messages '
                                     'WHERE length(text)>0 ORDER BY id DESC LIMIT ? OFFSET ?',
                                     (limit+1,off)).fetchall()
                items = [(r['id'], f"#{r['id']} · {preview(r['text'],39)}") for r in rows[:limit]]
                screen = menu.list_records('💬 Сообщения',items,page,'messages','msg',
                                           len(rows)>limit,'archive')
            elif section == 'media':
                rows = db.db.execute('SELECT id,kind,byte_count FROM media '
                                     'ORDER BY id DESC LIMIT ? OFFSET ?', (limit+1,off)).fetchall()
                items = [(r['id'],f"#{r['id']} · {r['kind']} · {r['byte_count']/1048576:.1f} МБ")
                         for r in rows[:limit]]
                screen = menu.list_records('📷 Фото и видео',items,page,'media','fileinfo',
                                           len(rows)>limit,'home')
            elif section == 'inboxmessages':
                rows=db.db.execute('SELECT id,user_id,text FROM dm_messages '
                                   'WHERE length(text)>0 ORDER BY id DESC LIMIT ? OFFSET ?',
                                   (limit+1,off)).fetchall()
                items=[(r['id'],f"#{r['id']} · от {r['user_id']}: {preview(r['text'],27)}")
                       for r in rows[:limit]]
                screen=menu.list_records('✉️ Входящие боту',items,page,'inboxmessages','imsg',
                                         len(rows)>limit,'inbox')
            else:
                rows=db.db.execute('SELECT id,kind,byte_count FROM dm_media '
                                   'ORDER BY id DESC LIMIT ? OFFSET ?', (limit+1,off)).fetchall()
                items=[(r['id'],f"#{r['id']} · {r['kind']} · {r['byte_count']/1048576:.1f} МБ")
                       for r in rows[:limit]]
                screen=menu.list_records('📷 Присланные боту файлы',items,page,'inboxmedia',
                                         'inboxfileinfo',len(rows)>limit,'inbox')
        elif section == 'deleted':
            page = page_arg()
            rows = db.deleted_recent(8,page)
            buttons=[]
            for r in rows:
                if r['archive_id']:
                    buttons.append([menu.row(
                        f"#{r['archive_id']} · чат {r['chat_id']}: " + preview(r['text'] or '',23),
                        f"ui:msg:{r['archive_id']}")])
                elif r['media_id']:
                    buttons.append([menu.row(f"Файл #{r['media_id']} · чат {r['chat_id']}",
                                             f"ui:fileinfo:{r['media_id']}")])
            screen = menu.Screen(
                f"🗑 ПОДТВЕРЖДЁННЫЕ УДАЛЕНИЯ · стр. {page}\n\n"
                + ('Telegram не сообщает обо всех удалениях. Нажмите на запись.' if rows else
                   'Подтверждённых удалений пока нет.'),
                buttons + menu.nav(page,'deleted',len(rows)==8,'archive'))
        elif section in ('msg','hist','imsg','ihist'):
            if len(parts)!=2:
                raise ValueError('Missing ID')
            mid = parse_int(parts[1])
            is_bot = section.startswith('i')
            entry = db.dm_get_message(mid) if is_bot else db.get_message(mid)
            previous = 'inboxmessages:1' if is_bot else 'messages:1'
            if entry is None:
                screen = menu.detail('Сообщение не найдено.', [], previous)
            elif section in ('hist','ihist'):
                revisions = db.dm_history(mid) if is_bot else db.history(mid)
                lines = [f"{r['revision_no']}. {preview(r['text'],180)}" for r in revisions[-12:]]
                screen = menu.detail('✏️ История сообщения #'+str(mid)+'\n\n'
                                     +'\n\n'.join(lines),
                                     [[menu.row('💬 К сообщению',f"ui:{'imsg' if is_bot else 'msg'}:{mid}")]],
                                     previous)
            else:
                author=entry['user_id'] if is_bot else entry['sender_id']
                info=(f"💬 Сообщение #{mid}\nОт: {author}\n"
                      f"Получено: {entry['received_at']}\n\n"
                      + (entry['text'] or '(без текста)'))
                screen=menu.detail(info,
                                   [[menu.row('✏️ История правок',
                                              f"ui:{'ihist' if is_bot else 'hist'}:{mid}")]],
                                   previous)
        elif section in ('fileinfo','inboxfileinfo'):
            if len(parts)!=2:
                raise ValueError('Missing ID')
            fid = parse_int(parts[1])
            is_bot = section == 'inboxfileinfo'
            record = db.dm_get_media(fid) if is_bot else db.get(fid)
            previous = 'inboxmedia:1' if is_bot else 'media:1'
            if record is None:
                screen = menu.detail('Файл не найден.', [], previous)
            else:
                can_send = cfg.bot_delivery and record['byte_count'] <= cfg.bot_send_max_mb*1048576
                message = (f"📷 Файл #{fid}\nТип: {record['kind']}\n"
                           f"Размер: {record['byte_count']/1048576:.1f} МБ\n\n"
                           + ('Нажмите «Получить файл».' if can_send else
                              'Выдача файлов выключена либо файл превышает установленный лимит.'))
                actions = ([[menu.row('📥 Получить файл',f"ui:{'ifetch' if is_bot else 'fetch'}:{fid}")]]
                           if can_send else [])
                screen=menu.detail(message,actions,previous)
        elif section in ('fetch','ifetch'):
            if len(parts)!=2:
                raise ValueError('Missing ID')
            fid=parse_int(parts[1])
            is_bot=section=='ifetch'
            record=db.dm_get_media(fid) if is_bot else db.get(fid)
            previous='inboxmedia:1' if is_bot else 'media:1'
            if not cfg.bot_delivery:
                screen=menu.detail('Выдача файлов отключена в настройках сервера.',[],previous)
            elif not record or record['byte_count'] > cfg.bot_send_max_mb*1048576:
                screen=menu.detail('Файл отсутствует или слишком большой для отправки.',[],previous)
            else:
                path=safe_archive_path(media_root,record['relative_path'])
                if not path or not path.is_file():
                    screen=menu.detail('Файл отсутствует на диске.',[],previous)
                else:
                    await controller.send_file(owner_id,str(path),caption=f"Файл архива #{fid}")
                    screen=menu.detail('✅ Файл отправлен в этот чат.',[],previous)
        elif section == 'people':
            rows=db.dm_people()
            info='\n'.join(f"{r['user_id']}: {'✅ согласен' if r['state']=='active' else '⏸ пауза'}"
                            for r in rows)
            screen=menu.detail('👤 Пользователи бота\n\n'+(info or 'Пока нет согласий.'),[],
                               'inbox')
        else:
            raise ValueError('Unknown menu')
        buttons = ui_buttons(screen.rows)
        if edit:
            await event.edit(next(chunks(screen.text, 3400), ''), buttons=buttons, parse_mode=None)
        else:
            await event.reply(next(chunks(screen.text, 3400), ''), buttons=buttons, parse_mode=None)

    @controller.on(events.CallbackQuery())
    async def bot_menu_callback(event):
        if not event.data:
            return
        if event.data.startswith(b'dm:'):
            if not event.is_private or event.sender_id is None or event.sender_id == owner_id:
                await event.answer('Недоступно.', alert=True)
                return
            person = event.sender_id
            action = event.data.decode('ascii', errors='ignore')[3:]
            if action == 'agree':
                db.dm_consent(person)
                message = ('✅ Согласие получено. Новые сообщения сохраняются. '
                           'Вы можете в любой момент остановить запись или удалить архив.')
            elif action == 'stop':
                db.dm_pause(person)
                message = ('⏸ Сохранение остановлено. Уже сохранённое остаётся. '
                           'Для полного удаления нажмите «Удалить свой архив».')
            elif action == 'forgetask':
                await event.answer()
                await event.edit('🗑 Вы действительно хотите удалить ваш архив сообщений и файлы '
                                 'из текущего хранилища? Резервные копии могут оставаться.',
                                 buttons=[[Button.inline('🗑 Да, удалить', b'dm:forgetyes'),
                                           Button.inline('Отмена', b'dm:cancel')]],parse_mode=None)
                return
            elif action == 'forgetyes':
                for relative in db.dm_forget(person):
                    path = safe_archive_path(media_root, relative)
                    if path is not None:
                        path.unlink(missing_ok=True)
                message = '🗑 Ваш архив удалён из текущего хранилища. Резервные копии могут оставаться.'
            elif action in ('privacy','cancel'):
                message = DM_NOTICE
            else:
                await event.answer('Неизвестное действие.', alert=True)
                return
            await event.answer()
            await event.edit(message, buttons=visitor_buttons(person),parse_mode=None)
            return
        if not event.data.startswith(b'ui:'):
            return
        # Callback buttons are editable/fabricatable; check owner on every press.
        if not event.is_private or event.sender_id != owner_id or event.chat_id != owner_id:
            await event.answer('Эта панель доступна только владельцу.', alert=True)
            return
        await event.answer()
        try:
            await ui_screen(event, event.data[3:].decode('ascii'), edit=True)
        except (ValueError, OverflowError):
            await controller.send_message(owner_id, '⚠️ Неверный пункт меню. Отправьте /start.')
        except Exception as exc:
            if type(exc).__name__ == 'MessageNotModifiedError':
                return
            LOG.exception('Owner interface action failed')
            await controller.send_message(owner_id,'⚠️ Не удалось выполнить действие. Попробуйте /start.')

    @controller.on(events.NewMessage(incoming=True))
    async def bot_command(event):
        if event.is_group:
            await handle_bot_group_message(event)
            return
        if not event.is_private or event.sender_id is None:
            return
        if event.sender_id != owner_id:
            person = event.sender_id
            message_text = (event.raw_text or '').strip()
            cmd = message_text.split(None,1)[0].lower() if message_text.startswith('/') else ''
            if cmd in ('/start','/privacy','/help'):
                await visitor_reply(event, person)
            elif cmd == '/agree':
                db.dm_consent(person)
                await event.reply('✅ Вы согласились. Новые сообщения сохраняются владельцу бота.',
                                  buttons=visitor_buttons(person),parse_mode=None)
            elif cmd == '/stop':
                db.dm_pause(person)
                await event.reply('⏸ Архивирование остановлено. Старый архив сохранён.',
                                  buttons=visitor_buttons(person),parse_mode=None)
            elif cmd == '/forget':
                files = db.dm_forget(person)
                for rel in files:
                    file = safe_archive_path(media_root,rel)
                    if file is not None:
                        file.unlink(missing_ok=True)
                await event.reply('🗑 Архив удалён из текущего хранилища. '
                                  'Резервные копии могут храниться отдельно.',
                                  buttons=visitor_buttons(person),parse_mode=None)
            elif not db.dm_is_active(person):
                await visitor_reply(event, person)
            elif cmd:
                await event.reply('Команды: /privacy /stop /forget. Обычные сообщения сохраняются.',
                                  parse_mode=None)
            else:
                await bot_direct_capture(event)
            return
        parts = (event.raw_text or "").strip().split()
        if not parts:
            return
        cmd = parts[0].split("@", 1)[0].lower()

        async def reply(text):
            await event.reply(text[:3900], parse_mode=None)

        if cmd in ('/start','/menu'):
            await ui_screen(event, 'home')
        elif cmd == '/help':
            await ui_screen(event, 'help')
        elif cmd == '/commands':
            await reply('Старые команды продолжают работать: /status /chats /private '
                        '/private_on /private_off /groups /group_on /group_off '
                        '/messages /message /history /deleted /recent /file '
                        '/inbox /inbox_media /inbox_file /people. Для кнопок: /start')
        elif cmd == "/status":
            st = db.stats()
            ms = db.message_stats()
            bg_total, bg_active = db.bot_group_count()
            dm = db.dm_count()
            await reply(f"✉️ Личные сообщения боту: {dm['texts']} текстовых, {dm['files']} файлов; "
                        f"согласились: {dm['people']}\n"
                        f"📦 Медиа: {st['total']} файлов, {st['size']/(1024*1024):.1f} МБ\n"
                        f"💬 Сообщения: {ms['total']} (с текстом: {ms['texts'] or 0})\n"
                        f"🗑 Подтверждённых удалений сообщений: {ms['deleted'] or 0}\n"
                        f"👁 Выбрано чатов через личный аккаунт: {len(db.watched())}\n"
                        f"👥 Группы с ботом: {bg_total} (активных: {bg_active})\n"
                        f"💾 Свободно: {shutil.disk_usage(cfg.data_dir).free/(1024**3):.1f} ГБ\n"
                        f"📲 Выдача файлов через бота: {'включена' if cfg.bot_delivery else 'отключена'}")
        elif cmd == "/watched":
            rows = db.watched()
            await reply("Выбранные чаты:\n" + "\n".join(f"{r['chat_id']}: {r['title']}" for r in rows) if rows else
                        "Список пуст. Используйте /chats и /allow <id>.")
        elif cmd == "/groups":
            rows = db.bot_groups()
            await reply("Группы с ботом:\n" + "\n".join(
                f"{row['chat_id']}: {row['title']} [{row['state']}]" for row in rows[:50]
            ) if rows else "Групп с ботом пока нет. Добавьте его в Telegram-группу.")
        elif cmd in ("/group_off", "/group_on"):
            if len(parts) != 2:
                await reply("Использование: /group_off <chat_id> или /group_on <chat_id>")
                return
            try:
                gid = int(parts[1])
            except ValueError:
                await reply("Неверный идентификатор группы")
                return
            state = db.bot_group_state(gid)
            if state is None or state == "left":
                await reply("Группа не зарегистрирована или бот покинул её")
                return
            if cmd == "/group_off":
                db.set_bot_group_state(gid, "paused")
                await reply(f"⏸ Архивирование группы {gid} остановлено")
                try:
                    await controller.send_message(gid, "⏸ Архивирование новых сообщений отключено владельцем бота.",
                                                  parse_mode=None)
                except Exception:
                    LOG.warning("Cannot post group pause notice: %s", gid)
            else:
                db.set_bot_group_state(gid, "pending")
                # Group reactivation must post a new disclosure first.
                class GroupRef:
                    chat_id = gid
                    is_group = True
                    async def get_chat(self):
                        return await controller.get_entity(gid)
                await announce_group(GroupRef())
                await reply("✅ Архивирование включено" if db.bot_group_active(gid)
                            else "Не удалось отправить уведомление группе: запись не включена")
        elif cmd in ('/private','/private_on','/private_off'):
            if cmd == '/private':
                page = int(parts[1]) if len(parts)==2 and parts[1].isdigit() else 1
                if not 1 <= page <= 100:
                    await reply('Номер страницы должен быть от 1 до 100')
                    return
                users = []
                async for dialog in personal.iter_dialogs():
                    if (getattr(dialog,'is_user',False)
                        and not getattr(dialog.entity,'bot',False)
                        and dialog.id != owner_id):
                        users.append(dialog)
                    if len(users) >= page*15:
                        break
                rows = users[(page-1)*15:page*15]
                await reply('💬 Личные чаты, страница '+str(page)+':\n'+
                            ('\n'.join(f"{d.id}: {' '.join(d.name.split())[:60]} "
                                       f"[{'✅' if db.is_allowed(d.id) else '⏸'}]" for d in rows)
                             if rows else 'Нет чатов')+
                            '\nВключить: /private_on <id>. Остановить: /private_off <id>.')
                return
            if len(parts)!=2:
                await reply('Укажите ID: /private_on <id> или /private_off <id>')
                return
            try:
                chat_id=int(parts[1])
            except ValueError:
                await reply('Неверный ID')
                return
            found=None
            async for dialog in personal.iter_dialogs():
                if (dialog.id==chat_id and getattr(dialog,'is_user',False)
                    and not getattr(dialog.entity,'bot',False) and chat_id!=owner_id):
                    found=' '.join(dialog.name.split())[:200]
                    break
            if found is None:
                await reply('Личная переписка не найдена. Используйте /private.')
                return
            if cmd=='/private_on':
                db.allow(chat_id,found)
                await reply('✅ Личная переписка включена: '+found+
                            '. Сохраняются только новые доступные сообщения. '
                            'Уведомьте собеседника о хранении переписки.')
            else:
                db.deny(chat_id)
                await reply('⏸ Сохранение личной переписки выключено: '+found)
        elif cmd == '/people':
            rows=db.dm_people()
            await reply('Согласие пользователей бота:\n'+
                        ('\n'.join(f"{r['user_id']}: {r['state']}" for r in rows)
                         if rows else 'Пока никто не согласился'))
        elif cmd == '/inbox':
            rows=db.dm_messages_recent(10)
            await reply('✉️ Новые сообщения самому боту:\n'+
                        ('\n'.join(f"#{r['id']} от={r['user_id']}: {preview(r['text'])}"
                                   for r in rows) if rows else 'Пока пусто'))
        elif cmd == '/inbox_message':
            if len(parts)!=2 or not parts[1].isdigit():
                await reply('Использование: /inbox_message <id>'); return
            row=db.dm_get_message(int(parts[1]))
            if row is None:
                await reply('Сообщение не найдено'); return
            await reply(f"✉️ #{row['id']} от={row['user_id']} время={row['received_at']}")
            for chunk in chunks(row['text'] or '(без текста)'):
                await reply(chunk)
        elif cmd == '/inbox_history':
            if len(parts)!=2 or not parts[1].isdigit():
                await reply('Использование: /inbox_history <id>'); return
            row=db.dm_get_message(int(parts[1]))
            if row is None:
                await reply('Сообщение не найдено'); return
            rows=db.dm_history(int(parts[1]))
            await reply('История правок:\n'+'\n'.join(
                f"{r['revision_no']}. {r['change_type']} {preview(r['text'],120)}"
                for r in rows[-15:]))
        elif cmd == '/inbox_media':
            rows=db.dm_media_recent(10)
            await reply('📷 Последние файлы, присланные боту:\n'+
                        ('\n'.join(f"#{r['id']} от={r['user_id']} {r['kind']} "
                                   f"{r['byte_count']/(1024*1024):.1f} МБ" for r in rows)
                         if rows else 'Пока пусто'))
        elif cmd == '/inbox_file':
            if not cfg.bot_delivery:
                await reply('Выдача файлов выключена: ENABLE_BOT_DELIVERY=true'); return
            if len(parts)!=2 or not parts[1].isdigit():
                await reply('Использование: /inbox_file <id>'); return
            row=db.dm_get_media(int(parts[1]))
            if row is None:
                await reply('Файл не найден'); return
            if row['byte_count']>cfg.bot_send_max_mb*1024*1024:
                await reply('Файл слишком большой для выдачи через бота'); return
            path=safe_archive_path(media_root,row['relative_path'])
            if path is None:
                await reply('Файл отсутствует на диске'); return
            await controller.send_file(owner_id,str(path),caption=f"DM файл #{row['id']}")
        elif cmd == "/chats":
            page = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 1
            if not 1 <= page <= 100:
                await reply("Номер страницы должен быть от 1 до 100")
                return
            start, end = (page - 1) * 15, page * 15
            items = []
            index = 0
            async for dialog in personal.iter_dialogs():
                if index >= end:
                    break
                if index >= start:
                    # Telegram titles may include line breaks and invisible controls.
                    title = " ".join(dialog.name.split())[:70]
                    items.append(f"{dialog.id}: {title}")
                index += 1
            await reply(f"Чаты, страница {page}:\n" + ("\n".join(items) if items else "Нет чатов") +
                        "\nВключить: /allow <chat_id>")
        elif cmd in ("/allow", "/deny"):
            if len(parts) != 2:
                await reply("Укажите идентификатор чата, например /allow -100123456")
                return
            try:
                chat_id = int(parts[1])
            except ValueError:
                await reply("Неверный идентификатор")
                return
            if cmd == "/deny":
                db.deny(chat_id)
                await reply(f"⏸ Чат {chat_id} выключен. Ранее сохранённые файлы не удалены.")
                return
            # Only enable chats actually joined by the personal account.
            found = None
            async for dialog in personal.iter_dialogs():
                if dialog.id == chat_id:
                    found = " ".join(dialog.name.split())
                    break
            if found is None:
                await reply("Чат не найден среди доступных личному аккаунту. Используйте /chats.")
                return
            db.allow(chat_id, found)
            await reply(f"✅ Добавлен чат: {found}\nНовые сообщения, доступные фото и видео будут сохраняться. "
                        "Прошлые сообщения не импортируются.")
        elif cmd == "/recent":
            rows = db.latest(10)
            await reply("Последние файлы:\n" + "\n".join(
                f"#{r['id']} {r['kind']} чат={r['chat_id']} "
                f"{r['byte_count']/(1024*1024):.1f} МБ" + (" 🗑" if r['telegram_deleted'] else "")
                for r in rows) if rows else "Архив пока пуст.")
        elif cmd == "/messages":
            rows = db.latest_messages(10)
            await reply("Последние текстовые сообщения:\n" + "\n".join(
                f"#{r['id']} чат={r['chat_id']} отправитель={r['sender_id']} "
                f"{preview(r['text'])}" + (" ✏️" if r['edited_at'] else "") +
                (" 🗑" if r['telegram_deleted'] else "") for r in rows
            ) if rows else "Текстовых сообщений пока нет.")
        elif cmd == "/message":
            if len(parts) != 2 or not parts[1].isdigit():
                await reply("Использование: /message <id> (см. /messages или /deleted)")
                return
            row = db.get_message(int(parts[1]))
            if row is None:
                await reply("Сообщение не найдено")
                return
            header = (f"Архив сообщения #{row['id']}\nЧат: {row['chat_id']}\n"
                      f"Отправитель: {row['sender_id']}\nПолучено: {row['received_at']}\n"
                      f"Статус: {'удаление подтверждено' if row['telegram_deleted'] else 'удаление не подтверждено'}\n"
                      f"Редактировалось: {'да' if row['edited_at'] else 'нет'}\n\n")
            await reply(header)
            for part in chunks(row["text"] or "(без текста)"):
                await reply(part)
        elif cmd == "/history":
            if len(parts) != 2 or not parts[1].isdigit():
                await reply("Использование: /history <id> (см. /messages)")
                return
            mid = int(parts[1])
            if db.get_message(mid) is None:
                await reply("Сообщение не найдено")
                return
            revisions = db.history(mid)
            await reply(f"Версии сообщения #{mid}:\n" + "\n".join(
                f"{r['revision_no']}. {r['observed_at']} ({r['change_type']}): {preview(r['text'],120)}"
                for r in revisions[-15:]) +
                "\nПолная текущая версия: /message " + str(mid))
        elif cmd == "/deleted":
            page = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 1
            if not 1 <= page <= 100:
                await reply("Номер страницы должен быть от 1 до 100")
                return
            rows = db.deleted_recent(10, page)
            await reply(f"Подтверждённые удаления, стр. {page}:\n" + "\n".join(
                f"чат={r['chat_id']} сообщение={r['message_id']} " +
                (f"текст #{r['archive_id']}: {preview(r['text'])}" if r['archive_id'] else "") +
                (f" медиа #{r['media_id']} ({r['media_kind']})" if r['media_id'] else "")
                for r in rows
            ) if rows else "Подтверждённых удалений пока нет. Telegram не всегда сообщает об удалениях.")
        elif cmd == "/skipped":
            rows = db.ignored_recent()
            await reply("Пропущенные медиа:\n" + "\n".join(
                f"чат={r['chat_id']} сообщение={r['message_id']}: {r['reason']}" for r in rows
            ) if rows else "Пропущенных медиа пока нет.")
        elif cmd == "/file":
            if not cfg.bot_delivery:
                await reply("Выдача файлов выключена. Администратор может включить ENABLE_BOT_DELIVERY=true")
                return
            if len(parts) != 2 or not parts[1].isdigit():
                await reply("Использование: /file <archive_id> (смотрите /recent)")
                return
            row = db.get(int(parts[1]))
            if row is None:
                await reply("Файл не найден")
                return
            if row["byte_count"] > cfg.bot_send_max_mb * 1024 * 1024:
                await reply("Файл слишком большой для выдачи через бота; сохранён на сервере")
                return
            path = safe_archive_path(media_root, row["relative_path"])
            if not path:
                await reply("Файл отсутствует на диске")
                return
            await controller.send_file(owner_id, str(path), caption=f"Архив #{row['id']}")
        else:
            await reply("Неизвестная команда. Отправьте /help")

    LOG.info("READY: personal user=%s control bot=%s (owner=%s); watched=%s bot_groups=%s",
             me.id, bot_me.id, owner_id, len(db.watched()), db.bot_group_count()[0])
    LOG.info("GROUP_BOT_ARCHIVE_V5: enabled=%s; direct_dm_consent=True; menu=ready", cfg.auto_group_archive)
    try:
        await asyncio.gather(personal.run_until_disconnected(), controller.run_until_disconnected())
    finally:
        await personal.disconnect()
        await controller.disconnect()
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())
