"""SQLite archive. Additive v4 migrations preserve all prior archives and sessions."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS bot_groups (
          chat_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending','active','paused','left')),
          joined_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          notice_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bot_groups_state ON bot_groups(state);
        CREATE TABLE IF NOT EXISTS watched (
          chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL,
          added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS media (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          sender_id INTEGER, kind TEXT NOT NULL, relative_path TEXT NOT NULL,
          byte_count INTEGER NOT NULL, received_at TEXT NOT NULL,
          telegram_deleted INTEGER NOT NULL DEFAULT 0,
          UNIQUE(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_media_recent ON media(id DESC);
        CREATE TABLE IF NOT EXISTS ignored (
          chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          reason TEXT NOT NULL, received_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          sender_id INTEGER, peer_scope TEXT NOT NULL DEFAULT 'unknown',
          text TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL,
          edited_at TEXT, telegram_deleted INTEGER NOT NULL DEFAULT 0,
          deleted_at TEXT,
          UNIQUE(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_recent ON messages(id DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_global_lookup
            ON messages(message_id, peer_scope);
        CREATE TABLE IF NOT EXISTS message_revisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          archive_message_id INTEGER NOT NULL REFERENCES messages(id),
          revision_no INTEGER NOT NULL,
          text TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          change_type TEXT NOT NULL,
          UNIQUE(archive_message_id, revision_no)
        );
        CREATE INDEX IF NOT EXISTS idx_revisions_archive ON
            message_revisions(archive_message_id, revision_no);
        """)
        # v4 bot direct messages live in their own namespace: message IDs are
        # not interchangeable between the personal Telegram account and bot.
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS dm_users (
          user_id INTEGER PRIMARY KEY,
          state TEXT NOT NULL CHECK(state IN ('active','paused')),
          consented_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dm_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          text TEXT NOT NULL DEFAULT '',
          received_at TEXT NOT NULL,
          edited_at TEXT,
          UNIQUE(user_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dm_messages_recent ON dm_messages(id DESC);
        CREATE TABLE IF NOT EXISTS dm_revisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          archive_message_id INTEGER NOT NULL REFERENCES dm_messages(id),
          revision_no INTEGER NOT NULL,
          text TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          change_type TEXT NOT NULL,
          UNIQUE(archive_message_id,revision_no)
        );
        CREATE TABLE IF NOT EXISTS dm_media (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          kind TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          byte_count INTEGER NOT NULL,
          received_at TEXT NOT NULL,
          UNIQUE(user_id,message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dm_media_recent ON dm_media(id DESC);
        """)
        # v1 databases have no deleted_at column: additive, backwards-compatible migration.
        names = {row["name"] for row in self.db.execute("PRAGMA table_info(media)")}
        if "deleted_at" not in names:
            self.db.execute("ALTER TABLE media ADD COLUMN deleted_at TEXT")

    def allow(self, chat_id: int, title: str):
        self.db.execute(
            "INSERT INTO watched(chat_id,title,added_at) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
            (chat_id, title[:200], now()),
        )

    def deny(self, chat_id: int):
        self.db.execute("DELETE FROM watched WHERE chat_id=?", (chat_id,))

    def is_allowed(self, chat_id: int) -> bool:
        return bool(self.db.execute("SELECT 1 FROM watched WHERE chat_id=?", (chat_id,)).fetchone())

    def watched(self):
        return self.db.execute("SELECT chat_id,title FROM watched ORDER BY title").fetchall()

    # Bot-in-group archival is independent of personal-account /allow.
    # A new group starts PENDING; no user messages may be recorded until the
    # bot has successfully posted a visible disclosure to that group.
    def register_bot_group(self, chat_id: int, title: str) -> str:
        if chat_id >= 0:
            raise ValueError("Only Telegram groups can be registered")
        ts = now()
        self.db.execute(
            "INSERT OR IGNORE INTO bot_groups(chat_id,title,state,joined_at,updated_at) "
            "VALUES(?,?,'pending',?,?)", (chat_id, title[:200], ts, ts)
        )
        self.db.execute("UPDATE bot_groups SET title=?,updated_at=? WHERE chat_id=?",
                        (title[:200], ts, chat_id))
        return self.bot_group_state(chat_id)

    def bot_group_state(self, chat_id: int) -> str | None:
        row = self.db.execute("SELECT state FROM bot_groups WHERE chat_id=?", (chat_id,)).fetchone()
        return row["state"] if row else None

    def activate_bot_group(self, chat_id: int):
        # Must only be called AFTER a successful disclosure message.
        self.db.execute(
            "UPDATE bot_groups SET state='active',notice_at=?,updated_at=? "
            "WHERE chat_id=? AND state IN ('pending','paused')", (now(), now(), chat_id)
        )

    def set_bot_group_state(self, chat_id: int, state: str) -> bool:
        # Never bypass the group disclosure: only activate_bot_group()
        # may set 'active', after the notice has been sent successfully.
        if state == "active":
            raise ValueError("Use activate_bot_group after successful disclosure")
        if state not in ("pending", "paused", "left"):
            raise ValueError("Invalid group state")
        cur = self.db.execute(
            "UPDATE bot_groups SET state=?,updated_at=? WHERE chat_id=?",
            (state, now(), chat_id),
        )
        return bool(cur.rowcount)

    def can_archive(self, chat_id: int) -> bool:
        """Check current consent and pause state, including during long downloads."""
        state = self.bot_group_state(chat_id)
        return state not in ("paused", "left") and (
            self.is_allowed(chat_id) or state == "active"
        )

    def bot_group_active(self, chat_id: int) -> bool:
        return self.bot_group_state(chat_id) == "active"

    def bot_groups(self):
        return self.db.execute(
            "SELECT chat_id,title,state,notice_at FROM bot_groups ORDER BY updated_at DESC"
        ).fetchall()

    def bot_group_count(self) -> tuple[int,int]:
        row = self.db.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) active "
            "FROM bot_groups"
        ).fetchone()
        return row["total"], row["active"] or 0

    # Existing media API, unchanged for v1 compatibility.
    def contains(self, chat_id: int, message_id: int) -> bool:
        return bool(self.db.execute(
            "SELECT 1 FROM media WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        ).fetchone())

    def save(self, chat_id: int, message_id: int, sender_id: int | None,
             kind: str, relative: str, size: int):
        # If Telegram deleted the message while a media download was still in
        # progress, preserve its confirmed deletion state on the media record.
        deleted = self.db.execute(
            "SELECT telegram_deleted,deleted_at FROM messages WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ).fetchone()
        self.db.execute(
            "INSERT OR IGNORE INTO media(chat_id,message_id,sender_id,kind,relative_path,byte_count,received_at,telegram_deleted,deleted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (chat_id, message_id, sender_id, kind, relative, size, now(),
             deleted["telegram_deleted"] if deleted else 0,
             deleted["deleted_at"] if deleted else None),
        )

    def save_if_allowed(self, chat_id: int, message_id: int, sender_id: int | None,
                        kind: str, relative: str, size: int) -> bool:
        """Atomically recheck archive consent before saving downloaded media."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.can_archive(chat_id):
                return False
            self.save(chat_id, message_id, sender_id, kind, relative, size)
            return True

    def ignore(self, chat_id: int, message_id: int, reason: str):
        self.db.execute(
            "INSERT OR IGNORE INTO ignored(chat_id,message_id,reason,received_at) VALUES(?,?,?,?)",
            (chat_id, message_id, reason, now()),
        )

    def capture_new(self, chat_id: int, message_id: int, sender_id: int | None,
                    text: str, peer_scope: str) -> bool:
        """Store *newly observed* messages, including captions/empty text for media.

        Never overwrite an earlier message: its original version must survive.
        """
        if peer_scope not in ("channel", "nonchannel"):
            raise ValueError("Invalid scope")
        timestamp = now()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # Consent may be withdrawn after the event handler's initial check.
            if not self.can_archive(chat_id):
                return False
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages(chat_id,message_id,sender_id,peer_scope,text,received_at) "
                "VALUES(?,?,?,?,?,?)",
                (chat_id, message_id, sender_id, peer_scope, text, timestamp),
            )
            if not cur.rowcount:
                return False
            self.db.execute(
                "INSERT INTO message_revisions(archive_message_id,revision_no,text,observed_at,change_type) "
                "VALUES(?,?,?,?,?)", (cur.lastrowid, 1, text, timestamp, "initial"),
            )
        return True

    def capture_edit(self, chat_id: int, message_id: int, text: str) -> bool:
        """Record subsequent versions *only* if the original was observed.

        Telegram editing older messages must not import chat history implicitly.
        """
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # The handler's consent check is not sufficient if permission
            # changes before the write transaction begins.
            if not self.can_archive(chat_id):
                return False
            row = self.db.execute(
                "SELECT id,text,telegram_deleted FROM messages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
            if row is None or row["text"] == text or row["telegram_deleted"]:
                return False
            revision_no = self.db.execute(
                "SELECT COALESCE(MAX(revision_no),0)+1 FROM message_revisions WHERE archive_message_id=?",
                (row["id"],),
            ).fetchone()[0]
            ts = now()
            self.db.execute(
                "INSERT INTO message_revisions(archive_message_id,revision_no,text,observed_at,change_type) "
                "VALUES(?,?,?,?,?)", (row["id"], revision_no, text, ts, "edit"),
            )
            self.db.execute("UPDATE messages SET text=?,edited_at=? WHERE id=?", (text, ts, row["id"]))
        return True

    def _mark_deleted(self, chat_id: int, ids: list[int]) -> int:
        if not ids:
            return 0
        count = 0
        ts = now()
        for mid in ids:
            cur = self.db.execute(
                "UPDATE messages SET telegram_deleted=1,deleted_at=? "
                "WHERE chat_id=? AND message_id=? AND telegram_deleted=0", (ts, chat_id, mid),
            )
            count += cur.rowcount
            cur = self.db.execute(
                "UPDATE media SET telegram_deleted=1,deleted_at=? "
                "WHERE chat_id=? AND message_id=? AND telegram_deleted=0", (ts, chat_id, mid),
            )
            count += cur.rowcount
        return count

    def marked_deleted(self, chat_id: int, ids: list[int]) -> int:
        """Confirmed chat-scoped deletion update (channels/supergroups)."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._mark_deleted(chat_id, ids)

    def marked_deleted_unscoped(self, ids: list[int]) -> int:
        """Private/basic-group deletes omit chat_id in Telethon.

        In this Telegram message scope IDs are global. Only mark if precisely
        one *watched* nonchannel message matches. Ignore uncertain/legacy rows.
        """
        count = 0
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for mid in ids:
                candidates = self.db.execute(
                    "SELECT m.chat_id FROM messages m JOIN watched w ON w.chat_id=m.chat_id "
                    "LEFT JOIN bot_groups b ON b.chat_id=m.chat_id "
                    "WHERE m.message_id=? AND m.peer_scope='nonchannel' "
                    "AND (b.state IS NULL OR b.state NOT IN ('paused','left'))", (mid,),
                ).fetchall()
                if len(candidates) == 1:
                    count += self._mark_deleted(candidates[0]["chat_id"], [mid])
        return count

    def latest(self, limit: int = 10):
        return self.db.execute(
            "SELECT id,chat_id,message_id,kind,byte_count,received_at,telegram_deleted "
            "FROM media ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()

    def get(self, file_id: int):
        return self.db.execute("SELECT * FROM media WHERE id=?", (file_id,)).fetchone()

    def latest_messages(self, limit: int = 10):
        return self.db.execute(
            "SELECT id,chat_id,message_id,sender_id,text,received_at,edited_at,telegram_deleted "
            "FROM messages WHERE length(text)>0 ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()

    def get_message(self, archive_id: int):
        return self.db.execute("SELECT * FROM messages WHERE id=?", (archive_id,)).fetchone()

    def history(self, archive_id: int):
        return self.db.execute(
            "SELECT revision_no,text,observed_at,change_type FROM message_revisions "
            "WHERE archive_message_id=? ORDER BY revision_no", (archive_id,),
        ).fetchall()

    def deleted_recent(self, limit: int = 10, page: int = 1):
        return self.db.execute("""
            SELECT 'message' AS entry_type, m.id AS archive_id, m.chat_id, m.message_id,
                   m.text AS text, COALESCE(m.deleted_at,m.received_at) AS deleted_at,
                   me.id AS media_id, me.kind AS media_kind
            FROM messages m LEFT JOIN media me
              ON me.chat_id=m.chat_id AND me.message_id=m.message_id
            WHERE m.telegram_deleted=1
            UNION ALL
            SELECT 'media' AS entry_type, NULL AS archive_id, me.chat_id, me.message_id,
                   NULL AS text, COALESCE(me.deleted_at,me.received_at) AS deleted_at,
                   me.id AS media_id, me.kind AS media_kind
            FROM media me WHERE me.telegram_deleted=1
              AND NOT EXISTS (
                 SELECT 1 FROM messages m WHERE m.chat_id=me.chat_id
                   AND m.message_id=me.message_id AND m.telegram_deleted=1
              )
            ORDER BY deleted_at DESC, message_id DESC LIMIT ? OFFSET ?
        """, (limit, (page - 1) * limit)).fetchall()

    def ignored_recent(self, limit: int = 10):
        return self.db.execute(
            "SELECT chat_id,message_id,reason FROM ignored ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def stats(self):
        return self.db.execute("SELECT COUNT(*) AS total,COALESCE(SUM(byte_count),0) AS size FROM media").fetchone()

    def message_stats(self):
        return self.db.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN length(text)>0 THEN 1 ELSE 0 END) AS texts, "
            "SUM(telegram_deleted) AS deleted FROM messages"
        ).fetchone()

    # Bot-direct messages: explicit visitor consent before ANY content capture.
    def dm_is_active(self, user_id: int) -> bool:
        row = self.db.execute("SELECT state FROM dm_users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["state"] == "active")

    def dm_consent(self, user_id: int):
        ts = now()
        self.db.execute(
            "INSERT INTO dm_users(user_id,state,consented_at,updated_at) "
            "VALUES(?,'active',?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "state='active',consented_at=excluded.consented_at,updated_at=excluded.updated_at",
            (user_id,ts,ts),
        )

    def dm_pause(self, user_id: int):
        self.db.execute("UPDATE dm_users SET state='paused',updated_at=? WHERE user_id=?", (now(),user_id))

    def dm_people(self, limit: int = 20):
        return self.db.execute("SELECT user_id,state,consented_at FROM dm_users "
                               "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()

    def dm_capture_new(self, user_id: int, message_id: int, text: str) -> bool:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.dm_is_active(user_id):
                return False
            ts = now()
            cur = self.db.execute("INSERT OR IGNORE INTO dm_messages(user_id,message_id,text,received_at) "
                                  "VALUES(?,?,?,?)", (user_id,message_id,text,ts))
            if not cur.rowcount:
                return False
            self.db.execute("INSERT INTO dm_revisions(archive_message_id,revision_no,text,observed_at,change_type) "
                            "VALUES(?,1,?,?,'initial')",(cur.lastrowid,text,ts))
            return True

    def dm_capture_edit(self,user_id: int,message_id: int,text: str) -> bool:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.dm_is_active(user_id):
                return False
            row=self.db.execute('SELECT id,text FROM dm_messages WHERE user_id=? AND message_id=?',
                                (user_id,message_id)).fetchone()
            if row is None or row['text']==text:
                return False
            ts=now()
            number=self.db.execute('SELECT COALESCE(MAX(revision_no),0)+1 FROM dm_revisions WHERE archive_message_id=?',
                                   (row['id'],)).fetchone()[0]
            self.db.execute("INSERT INTO dm_revisions(archive_message_id,revision_no,text,observed_at,change_type) "
                            "VALUES(?,?,?,?,'edit')",(row['id'],number,text,ts))
            self.db.execute('UPDATE dm_messages SET text=?, edited_at=? WHERE id=?',(text,ts,row['id']))
            return True

    def dm_contains(self,user_id: int,message_id: int) -> bool:
        return bool(self.db.execute('SELECT 1 FROM dm_media WHERE user_id=? AND message_id=?',
                                    (user_id,message_id)).fetchone())

    def dm_save(self,user_id: int,message_id: int,kind: str,relative_path: str,size: int) -> bool:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.dm_is_active(user_id):
                return False
            cur=self.db.execute("INSERT OR IGNORE INTO dm_media(user_id,message_id,kind,relative_path,byte_count,received_at) "
                                "VALUES(?,?,?,?,?,?)",(user_id,message_id,kind,relative_path,size,now()))
            return bool(cur.rowcount)

    def dm_messages_recent(self,limit: int=10):
        return self.db.execute('SELECT id,user_id,text,received_at,edited_at FROM dm_messages '
                               'WHERE length(text)>0 ORDER BY id DESC LIMIT ?', (limit,)).fetchall()

    def dm_get_message(self,id: int):
        return self.db.execute('SELECT * FROM dm_messages WHERE id=?',(id,)).fetchone()

    def dm_history(self,id: int):
        return self.db.execute('SELECT * FROM dm_revisions WHERE archive_message_id=? ORDER BY revision_no',(id,)).fetchall()

    def dm_media_recent(self,limit: int=10):
        return self.db.execute('SELECT id,user_id,kind,byte_count FROM dm_media ORDER BY id DESC LIMIT ?',
                               (limit,)).fetchall()

    def dm_get_media(self,id: int):
        return self.db.execute('SELECT * FROM dm_media WHERE id=?',(id,)).fetchone()

    def dm_count(self):
        return self.db.execute('SELECT (SELECT COUNT(*) FROM dm_messages) texts, '
                               '(SELECT COUNT(*) FROM dm_media) files, '
                               "(SELECT COUNT(*) FROM dm_users WHERE state='active') people").fetchone()

    def dm_forget(self,user_id: int) -> list[str]:
        """Erase a person's DM archive, return file names to unlink outside DB."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            files=[r['relative_path'] for r in self.db.execute(
                'SELECT relative_path FROM dm_media WHERE user_id=?',(user_id,)).fetchall()]
            self.db.execute('DELETE FROM dm_media WHERE user_id=?',(user_id,))
            self.db.execute('DELETE FROM dm_revisions WHERE archive_message_id IN '
                            '(SELECT id FROM dm_messages WHERE user_id=?)',(user_id,))
            self.db.execute('DELETE FROM dm_messages WHERE user_id=?',(user_id,))
            self.db.execute('DELETE FROM dm_users WHERE user_id=?',(user_id,))
        return files

    def close(self):
        self.db.close()
