#!/usr/bin/env bash
# Read-only preflight for the running Hetzner installation.
set -Eeuo pipefail
PROJECT_DIR="${PROJECT_DIR:-/home/monitor/TelegramMediaArchive_v1}"
PROJECT_NAME="telegram-media-archive"
CONTAINER_NAME="telegram-media-archive-archive-1"

cd "$PROJECT_DIR"
test -f compose.yaml || { echo "PREFLIGHT_FAIL: compose.yaml missing"; exit 1; }
test -f .env || { echo "PREFLIGHT_FAIL: .env missing"; exit 1; }
test -d .git || { echo "PREFLIGHT_FAIL: Git repository missing"; exit 1; }
command -v docker >/dev/null || { echo "PREFLIGHT_FAIL: Docker CLI missing"; exit 1; }

# Use sudo solely to query Docker; this script never mutates containers or volumes.
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
fi
"${docker_cmd[@]}" info >/dev/null 2>&1 || { echo "PREFLIGHT_FAIL: no noninteractive Docker access"; exit 1; }

actual_project=$("${docker_cmd[@]}" inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER_NAME")
actual_service=$("${docker_cmd[@]}" inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER_NAME")
running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$CONTAINER_NAME")
test "$actual_project" = "$PROJECT_NAME" && test "$actual_service" = archive && test "$running" = true || {
  echo "PREFLIGHT_FAIL: live container identity or state differs"; exit 1;
}

# Verify persistent archive data mount without printing sensitive paths/contents.
data_mount=$("${docker_cmd[@]}" inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}:{{.Name}}{{end}}{{end}}' "$CONTAINER_NAME")
case "$data_mount" in
  volume:*) echo "DATA_MOUNT_OK: named Docker volume" ;;
  *) echo "PREFLIGHT_FAIL: /data is not a named Docker volume"; exit 1 ;;
esac

git check-ignore -q .env || { echo "PREFLIGHT_FAIL: .env is not ignored by Git"; exit 1; }
"${docker_cmd[@]}" compose -p "$PROJECT_NAME" -f compose.yaml config --quiet >/dev/null
echo "COMPOSE_CONFIG_OK"
echo "LIVE_CONTAINER_OK"
echo "PREFLIGHT_OK"
echo "NOTICE: no backup or deploy was executed"
