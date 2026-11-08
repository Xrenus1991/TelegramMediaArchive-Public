#!/usr/bin/env python3
"""Conservative Telegram Media Archive backup retention; dry-run by default.

Policy (UTC calendar age):
- days 0..6: keep every backup
- days 7..29: keep newest backup per UTC day
- days 30..89: keep newest backup per ISO calendar week
- 90+ days: eligible for removal
Additionally protect the five newest backups, irrespective of their age.
Automatic deletion is NOT enabled by installing this file.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

DEFAULT_ROOT = Path("/home/monitor/telegram-media-archive-backups")
RELEASE_ROOT = Path("/home/monitor/telegram-media-archive-releases")
PROJECT_DIR = Path("/home/monitor/TelegramMediaArchive_v1")
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
STAMP_REGEX = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")


@dataclass(frozen=True)
class Snapshot:
    path: Path
    timestamp: datetime


def discover(root: Path) -> list[Snapshot]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("BACKUP_ROOT_MISSING_OR_SYMLINK")
    found = []
    for entry in root.iterdir():
        if entry.is_symlink():
            raise RuntimeError("BACKUP_ROOT_CONTAINS_SYMLINK")
        if not entry.is_dir() or not STAMP_REGEX.fullmatch(entry.name):
            continue
        timestamp = datetime.strptime(entry.name, STAMP_FORMAT).replace(tzinfo=timezone.utc)
        found.append(Snapshot(entry, timestamp))
    return sorted(found, key=lambda item: item.timestamp, reverse=True)


def plan(snapshots: list[Snapshot], now: datetime) -> tuple[list[Snapshot], list[Snapshot]]:
    """Pure function: decide which timestamped directories are retained."""
    if now.tzinfo is None:
        raise ValueError("UTC-aware datetime required")
    kept = []
    removable = []
    days_kept: set[str] = set()
    weeks_kept: set[tuple[int, int]] = set()
    for index, item in enumerate(sorted(snapshots, key=lambda s: s.timestamp, reverse=True)):
        age = (now.date() - item.timestamp.date()).days
        day = item.timestamp.strftime("%Y-%m-%d")
        iso = item.timestamp.isocalendar()
        week = (iso.year, iso.week)
        if index < 5 or age <= 6:
            keep = True
        elif age <= 29:
            keep = day not in days_kept
        elif age <= 89:
            keep = week not in weeks_kept
        else:
            keep = False
        if keep:
            kept.append(item)
            days_kept.add(day)
            weeks_kept.add(week)
        else:
            removable.append(item)
    return kept, removable


def ensure_valid_snapshot(snapshot: Snapshot, root: Path) -> None:
    path = snapshot.path
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.parent != root
        or path.resolve().parent != root.resolve()
        or not STAMP_REGEX.fullmatch(path.name)
        or path.stat().st_uid != os.getuid()
    ):
        raise RuntimeError("UNSAFE_BACKUP_DIRECTORY: " + path.name)
    for mandatory in (".env", "compose.yaml", "data/user.session"):
        candidate = path / mandatory
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("BACKUP_INCOMPLETE: " + path.name)


def check_keepers(kept: list[Snapshot]) -> None:
    if len(kept) < 5:
        raise RuntimeError("NOT_ENOUGH_PROTECTED_BACKUPS")
    verifier = PROJECT_DIR / "scripts/verify_backup.py"
    if not verifier.is_file():
        raise RuntimeError("BACKUP_VERIFIER_NOT_FOUND")
    for snapshot in kept[:2]:
        check = subprocess.run(
            [sys.executable, str(verifier), str(snapshot.path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            timeout=120, check=False,
        )
        if check.returncode != 0:
            raise RuntimeError("PROTECTED_BACKUP_VERIFICATION_FAILED: " + snapshot.path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Preview only for alternate roots")
    parser.add_argument("--apply", action="store_true", help="Delete eligible backups after safety checks")
    args = parser.parse_args()
    root = args.root
    snapshots = discover(root)
    kept, removable = plan(snapshots, datetime.now(timezone.utc))
    print("BACKUPS_TOTAL=" + str(len(snapshots)))
    print("BACKUPS_RETAINED=" + str(len(kept)))
    print("BACKUPS_ELIGIBLE=" + str(len(removable)))
    for item in removable:
        print("DELETE_CANDIDATE=" + item.path.name)
    if not args.apply:
        print("BACKUP_RETENTION_DRY_RUN_ONLY")
        return 0
    if root != DEFAULT_ROOT or root.resolve() != DEFAULT_ROOT:
        raise RuntimeError("APPLY_ALLOWED_ONLY_FOR_DEFAULT_ROOT")
    if root.stat().st_uid != os.getuid():
        raise RuntimeError("BACKUP_ROOT_NOT_OWNED_BY_USER")
    if not removable:
        print("NO_BACKUPS_DELETED")
        return 0
    if not snapshots or (datetime.now(timezone.utc) - snapshots[0].timestamp).total_seconds() > 7 * 86400:
        raise RuntimeError("NO_FRESH_BACKUP_WITHIN_7_DAYS")
    RELEASE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (RELEASE_ROOT / "auto-pull.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("DEPLOY_IN_PROGRESS_REFUSE_PRUNE") from None
        # Rescan while holding the deployment lock to avoid stale plans.
        snapshots = discover(root)
        kept, removable = plan(snapshots, datetime.now(timezone.utc))
        if not snapshots or (datetime.now(timezone.utc) - snapshots[0].timestamp).total_seconds() > 7 * 86400:
            raise RuntimeError("NO_FRESH_BACKUP_WITHIN_7_DAYS")
        check_keepers(kept)
        for item in removable:
            ensure_valid_snapshot(item, root)
        for item in removable:
            shutil.rmtree(item.path)
            print("BACKUP_DELETED=" + item.path.name, flush=True)
    print("BACKUP_RETENTION_APPLIED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        print("BACKUP_RETENTION_ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
