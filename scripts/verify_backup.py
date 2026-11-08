#!/usr/bin/env python3
"""Read-only verification of a completed Telegram Media Archive backup."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("USAGE: python3 scripts/verify_backup.py /absolute/path/to/backup")

root = Path(sys.argv[1]).resolve()
data = root / "data"
if not data.is_dir() or data.is_symlink():
    raise SystemExit("BACKUP_DATA_MISSING")
if not (root / ".env").is_file():
    raise SystemExit("BACKUP_CONFIG_MISSING")
if not (data / "user.session").is_file():
    raise SystemExit("BACKUP_SESSION_MISSING")
with (data / "user.session").open("rb") as session_file:
    if session_file.read(16) != b"SQLite format 3\x00":
        raise SystemExit("BACKUP_SESSION_NOT_SQLITE")
with sqlite3.connect("file:" + str(data / "user.session") + "?mode=ro", uri=True) as session:
    if session.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("BACKUP_SESSION_SQLITE_INVALID")
dbs = [p for p in data.rglob("*") if p.is_file() and p.suffix in (".db", ".sqlite", ".sqlite3")]
if not dbs:
    raise SystemExit("BACKUP_DATABASE_MISSING")
media = data / "media"
media_count = sum(1 for p in media.rglob("*") if p.is_file()) if media.is_dir() else 0
with tempfile.TemporaryDirectory() as temp:
    for index, source in enumerate(dbs):
        restored = Path(temp) / f"restore_{index}.sqlite"
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(restored)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit("BACKUP_SQLITE_INVALID")
        finally:
            dst.close()
            src.close()
print(f"BACKUP_DATABASES_VERIFIED={len(dbs)}")
print(f"BACKUP_MEDIA_FILES={media_count}")
print("BACKUP_SESSION_PRESENT")
print("BACKUP_RESTORE_TEST_OK")
print("NOTICE: media completeness and live Telegram authentication are not tested")
