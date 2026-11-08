#!/usr/bin/env bash
# One-time local server setup, never executed by a GitHub-hosted runner.
set -Eeuo pipefail
umask 077
project="/home/monitor/TelegramMediaArchive_v1"
key="/home/monitor/.ssh/tma_autopull_ro"
root="/home/monitor/telegram-media-archive-releases"
cd "$project"
case "${1:-}" in
  prepare)
    test "$(id -un)" = "monitor" || { echo "RUN_AS_MONITOR"; exit 1; }
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    if test ! -e "$key"; then
      ssh-keygen -q -t ed25519 -N '' -C tma-autopull-readonly -f "$key"
      echo "NEW_READONLY_GITHUB_KEY_CREATED"
    else
      test -f "$key.pub" || { echo "PUBLIC_KEY_MISSING"; exit 1; }
      echo "READONLY_GITHUB_KEY_ALREADY_PRESENT"
    fi
    chmod 600 "$key"
    chmod 644 "$key.pub"
    echo "ADD_PUBLIC_KEY_TO_GITHUB_AS_READ_ONLY_DEPLOY_KEY:"
    cat "$key.pub"
    echo "READ_ONLY_KEY_PREPARED"
    ;;
  verify)
    test -r "$key" || { echo "RUN_PREPARE_FIRST"; exit 1; }
    bash "$project/scripts/auto_pull_ready_on_server.sh" --check
    ;;
  enable)
    test "$(id -un)" = "monitor" || { echo "RUN_AS_MONITOR"; exit 1; }
    test -f "$root/current.sha" || {
      echo "ENABLE_REFUSED: first controlled deployment not yet verified"; exit 1;
    }
    bash "$project/scripts/auto_pull_ready_on_server.sh" --check
    sudo install -m 0644 "$project/scripts/systemd/telegram-media-archive-autopull.service" \
      /etc/systemd/system/telegram-media-archive-autopull.service
    sudo install -m 0644 "$project/scripts/systemd/telegram-media-archive-autopull.timer" \
      /etc/systemd/system/telegram-media-archive-autopull.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now telegram-media-archive-autopull.timer
    echo "OUTBOUND_AUTO_DEPLOY_ENABLED"
    ;;
  status)
    sudo systemctl status telegram-media-archive-autopull.timer --no-pager || true
    ;;
  disable)
    sudo systemctl disable --now telegram-media-archive-autopull.timer
    echo "OUTBOUND_AUTO_DEPLOY_DISABLED"
    ;;
  *)
    echo "USAGE: $0 prepare|verify|enable|status|disable"
    exit 2
    ;;
esac
