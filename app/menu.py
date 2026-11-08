"""Russian-language, mobile-first inline menu for the archive owner.

Pure functions: no Telegram/network imports. Every callback key is short and
must be re-authorized by the runtime handler (buttons are not permissions).
"""
from __future__ import annotations

from dataclasses import dataclass
import re

@dataclass
class Screen:
    text: str
    rows: list[list[tuple[str, str]]]


def clean_title(value: str, width: int = 29) -> str:
    value = re.sub(r"[\x00-\x1f\x7f\u200b-\u200f]", " ", value or "")
    value = " ".join(value.split()) or "Без названия"
    return value[:width - 1] + "…" if len(value) > width else value


def row(label: str, data: str):
    if not 1 <= len(data.encode("utf-8")) <= 64:
        raise ValueError("Telegram callback_data must be 1..64 bytes")
    return (label, data)


def back(where: str = "home"):
    return [row("🏠 Главное меню" if where == "home" else "◀️ Назад", "ui:" + where)]


def main() -> Screen:
    return Screen(
        "📦 TELEGRAM MEDIA ARCHIVE\n\n"
        "Выберите раздел — вводить команды не нужно.\n"
        "Архивируются только доступные новые сообщения из выбранных чатов.\n"
        "Одноразовые медиа не поддерживаются.",
        [
            [row("💬 Личные чаты", "ui:private:1"), row("👥 Группы", "ui:groups:1")],
            [row("📚 Архив сообщений", "ui:archive"), row("📷 Фото и видео", "ui:media")],
            [row("✉️ Пишут боту", "ui:inbox"), row("🗑 Удалённые", "ui:deleted:1")],
            [row("📊 Состояние", "ui:status"), row("⚙️ Настройки", "ui:settings")],
            [row("❓ Помощь", "ui:help")],
        ],
    )


def nav(page: int, prefix: str, has_next: bool, where: str = "home") -> list[list[tuple[str, str]]]:
    line = []
    if page > 1:
        line.append(row("◀️ Назад", f"ui:{prefix}:{page-1}"))
    line.append(row(f"Стр. {page}", f"ui:{prefix}:{page}"))
    if has_next:
        line.append(row("Далее ▶️", f"ui:{prefix}:{page+1}"))
    return [line, back(where)]


def dialogs(items: list[tuple[int, str, bool]], page: int, has_next: bool) -> Screen:
    buttons = []
    for chat_id, title, enabled in items:
        buttons.append([row(("✅ " if enabled else "⏸ ") + clean_title(title), f"ui:chat:{chat_id}")])
    return Screen(
        f"💬 ЛИЧНЫЕ ЧАТЫ · страница {page}\n\n"
        "✅ запись включена    ⏸ выключена\n"
        "Нажмите на переписку, чтобы управлять сохранением.\n"
        "Перед включением убедитесь, что собеседник знает об архивировании."
        if items else f"💬 Личные чаты · стр. {page}\n\nНичего не найдено.",
        buttons + nav(page, "private", has_next),
    )


def groups(items: list[tuple[int, str, str]], page: int, has_next: bool) -> Screen:
    badges = {"active": "✅", "paused": "⏸", "pending": "⏳", "left": "🚪"}
    buttons = [[row(f"{badges.get(state,'❔')} {clean_title(title)}", f"ui:group:{chat_id}")]
               for chat_id, title, state in items]
    return Screen(
        f"👥 ГРУППЫ · страница {page}\n\n"
        "✅ архивируется  ⏸ пауза  ⏳ ожидает уведомления\n"
        "Выберите группу. При включении бот уведомит всех участников."
        if items else "👥 Группы\n\nГрупп нет. Добавьте бота в группу, чтобы она появилась здесь.",
        buttons + nav(page, "groups", has_next),
    )


def confirm_chat(chat_id: int, title: str, enabled: bool) -> Screen:
    action = "остановить" if enabled else "включить"
    note = ("Ранее сохранённые файлы останутся в архиве." if enabled else
            "Будут сохраняться только новые сообщения. Уведомьте собеседника и убедитесь в согласии.")
    return Screen(
        f"💬 {clean_title(title, 65)}\n\nСтатус: {'✅ архивируется' if enabled else '⏸ выключено'}\n"
        f"Хотите {action} архивирование?\n\n{note}",
        [[row("✅ Подтвердить", f"ui:chatset:{chat_id}:{0 if enabled else 1}"),
          row("❌ Отмена", "ui:private:1")], back("private:1")],
    )


def confirm_group(chat_id: int, title: str, state: str) -> Screen:
    enable = state != "active"
    return Screen(
        f"👥 {clean_title(title, 65)}\nСтатус: {state}\n\n"
        + ("Включить архивирование? Перед запуском бот опубликует уведомление в группе."
           if enable else "Приостановить архивирование новых сообщений? Старые файлы останутся."),
        [[row("✅ Подтвердить", f"ui:groupset:{chat_id}:{1 if enable else 0}"),
          row("❌ Отмена", "ui:groups:1")], back("groups:1")],
    )


def archive() -> Screen:
    return Screen("📚 АРХИВ\n\nЧто хотите посмотреть?",
                  [[row("💬 Сообщения", "ui:messages:1"), row("🗑 Удалённые", "ui:deleted:1")],
                   [row("✏️ История правок", "ui:messages:1"), row("📷 Медиа", "ui:media")],
                   back()])


def inbox() -> Screen:
    return Screen("✉️ СООБЩЕНИЯ БОТУ\n\nСохраняются только сообщения пользователей, давших согласие.",
                  [[row("💬 Тексты", "ui:inboxmessages:1"), row("📷 Файлы", "ui:inboxmedia:1")],
                   [row("👤 Согласившиеся", "ui:people")], back()])


def list_records(title: str, items: list[tuple[int, str]], page: int,
                 prefix: str, detail: str, has_next: bool, back_to: str) -> Screen:
    buttons = [[row(clean_title(label, 48), f"ui:{detail}:{item_id}")]
               for item_id, label in items]
    return Screen(f"{title} · страница {page}\n\n"
                  + ("Нажмите на запись, чтобы открыть." if items else "Записей пока нет."),
                  buttons + nav(page, prefix, has_next, back_to))


def detail(text: str, rows: list[list[tuple[str, str]]], where: str) -> Screen:
    return Screen(text[:3850], rows + [back(where)])


def help_screen() -> Screen:
    return Screen(
        "❓ КАК ПОЛЬЗОВАТЬСЯ\n\n"
        "• «Личные чаты»: выберите переписку и включите запись.\n"
        "• «Группы»: добавьте бота в группу; сообщение об архиве появится до записи.\n"
        "• «Архив»: тексты, правки и подтверждённые удаления.\n"
        "• «Фото и видео»: просмотр и выдача файлов, если включена.\n"
        "• «Пишут боту»: входящие от пользователей, согласившихся на хранение.\n\n"
        "Удаление в Telegram обнаруживается не всегда. Одноразовые фото «посмотреть один раз» "
        "и секретные чаты не сохраняются. Старые сообщения не импортируются.\n\n"
        "Команды /help и /status по-прежнему работают.",
        [back()],
    )
