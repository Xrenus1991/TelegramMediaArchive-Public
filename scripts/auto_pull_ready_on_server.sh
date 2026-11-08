#!/usr/bin/env bash
# Poll a CI-tested release using outbound SSH to GitHub.
# Usage: bash scripts/auto_pull_ready_on_server.sh --check|--run
set -Eeuo pipefail
umask 077
cd "/home/monitor/TelegramMediaArchive_v1"
PROJECT_DIR="$PWD"
case "${1:-}" in
  --check|--run) MODE="$1" ;;
  *) echo "USAGE: $0 --check|--run" >&2; exit 2 ;;
esac
test "$(git remote get-url origin)" = "git@github.com:Xrenus1991/TelegramMediaArchive.git" || {
  echo "PULL_FAILED: unexpected Git origin" >&2; exit 1;
}
key="/home/monitor/.ssh/tma_autopull_ro"
test -f "$key" || { echo "PULL_NOT_CONFIGURED: read-only key missing" >&2; exit 1; }
test -f scripts/ci_receive_deploy.py || {
  echo "PULL_NOT_CONFIGURED: receiver missing" >&2; exit 1;
}
mkdir -p "/home/monitor/telegram-media-archive-releases"
exec 9>"/home/monitor/telegram-media-archive-releases/auto-pull.lock"
if ! flock -n 9; then
  echo "PULL_SKIPPED_ALREADY_RUNNING"
  exit 0
fi
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15"
if ! timeout 120 git fetch --no-tags origin refs/heads/deploy-ready 1>&2; then
  echo "PULL_FAILED: unable to fetch tested release ref" >&2
  exit 1
fi
sha="$(git rev-parse --verify 'FETCH_HEAD^{commit}')"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "PULL_FAILED: invalid release commit SHA" >&2; exit 1;
}
marker="/home/monitor/telegram-media-archive-releases/current.sha"
current="not-set"
if test -f "$marker"; then current="$(cat "$marker")"; fi
echo "TESTED_RELEASE=$sha"
if test "$sha" = "$current"; then
  echo "ALREADY_DEPLOYED"
  exit 0
fi
echo "PENDING_RELEASE=$sha"
if test "$MODE" = "--check"; then
  echo "PULL_CHECK_OK (no deployment performed)"
  exit 0
fi
bash "$PROJECT_DIR/scripts/preflight_on_server.sh"
tmp="$(mktemp -d "/home/monitor/telegram-media-archive-releases/package.XXXXXXXX")"
trap 'rm -rf "$tmp"' EXIT
git archive --format=tar.gz "$sha" -- app Dockerfile requirements.txt .dockerignore > "$tmp/release.tar.gz"
test -s "$tmp/release.tar.gz" || {
  echo "PULL_FAILED: release archive empty" >&2; exit 1;
}
echo "RUNNING_VERIFIED_RELEASE=$sha"
SSH_ORIGINAL_COMMAND="deploy $sha" /usr/bin/python3 \
  "$PROJECT_DIR/scripts/ci_receive_deploy.py" < "$tmp/release.tar.gz"
echo "OUTBOUND_DEPLOY_COMPLETED=$sha"
