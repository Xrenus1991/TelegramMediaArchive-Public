#!/usr/bin/env bash
set -Eeuo pipefail
cd "$HOME/telegram-media-archive"

echo '=== TELEGRAM MEDIA ARCHIVE V1: SEPARATE PROJECT ==='
echo 'Other Docker Compose projects will not be stopped or changed.'
echo '=== SECURE CONFIGURATION ==='
python3 scripts/configure.py

echo '=== BUILD ISOLATED ARCHIVE CONTAINER ==='
sudo docker compose -p telegram-media-archive build archive

echo '=== AUTHENTICATE YOUR PERSONAL TELEGRAM (separate new session) ==='
sudo docker compose -p telegram-media-archive run --rm --no-deps archive python -m app.login

echo '=== LAUNCH ARCHIVE (NO CHATS SELECTED YET) ==='
sudo docker compose -p telegram-media-archive up -d archive

for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if sudo docker compose -p telegram-media-archive logs --no-color --tail=40 archive 2>&1 | grep -q 'READY:'; then
    echo 'TELEGRAM_MEDIA_ARCHIVE_INSTALLED'
    echo 'Open your NEW control bot, send /start, then /chats and /allow <chat_id>.'
    echo 'No chats were enabled automatically. View-once and protected media are skipped.'
    exit 0
  fi
  sleep 2
done

echo 'ARCHIVE_NOT_READY: installation incomplete. Check logs privately; do not reveal secrets.' >&2
sudo docker compose -p telegram-media-archive ps archive
exit 1
