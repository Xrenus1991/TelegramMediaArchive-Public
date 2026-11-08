#!/usr/bin/env bash
# Prepare and test a candidate image without touching the running archive.
set -Eeuo pipefail
umask 077
cd "${PROJECT_DIR:-/home/monitor/TelegramMediaArchive_v1}"
bash scripts/preflight_on_server.sh
test -z "$(git status --porcelain)" || { echo "STAGE_FAIL: working tree has local modifications"; exit 1; }
sha="$(git rev-parse --short=12 HEAD)"
case "$sha" in *[!a-f0-9]*|'') echo "STAGE_FAIL: unexpected git sha"; exit 1;; esac
tag="telegram-media-archive-candidate:$sha"
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo -n docker); fi
"${docker_cmd[@]}" info >/dev/null 2>&1 || { echo "STAGE_FAIL: Docker inaccessible"; exit 1; }
echo "=== BACKUP BEFORE BUILD ==="
backup_output="$(bash scripts/backup_on_server.sh)"
printf '%s\n' "$backup_output"
backup_path="$(printf '%s\n' "$backup_output" | sed -n 's/^BACKUP_CREATED=//p' | tail -1)"
test -n "$backup_path" || { echo "STAGE_FAIL: backup not completed"; exit 1; }
python3 scripts/verify_backup.py "$backup_path"
echo "=== BUILD CANDIDATE (NO DEPLOY) ==="
"${docker_cmd[@]}" build --tag "$tag" --file Dockerfile .
echo "=== OFFLINE COMPILE CHECK ==="
"${docker_cmd[@]}" run --rm --network none --read-only --entrypoint python "$tag" -c 'import ast,pathlib; files=list(pathlib.Path("/srv/app").rglob("*.py")); [ast.parse(p.read_text(encoding="utf-8"),filename=str(p)) for p in files]; print("PYTHON_SYNTAX_OK="+str(len(files)))'
echo "CANDIDATE_IMAGE=$tag"
echo "STAGE_CANDIDATE_OK"
echo "NOTICE: live archive was not stopped or replaced"
