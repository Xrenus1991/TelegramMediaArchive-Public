#!/usr/bin/env bash
# Read-only deployment gate: verify candidate and record current image.
set -Eeuo pipefail
cd "${PROJECT_DIR:-/home/monitor/TelegramMediaArchive_v1}"
bash scripts/preflight_on_server.sh
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo -n docker); fi
sha=$(git rev-parse --short=12 HEAD)
candidate="telegram-media-archive-candidate:$sha"
"${docker_cmd[@]}" image inspect "$candidate" >/dev/null || { echo "CANDIDATE_NOT_FOUND"; exit 1; }
current_image=$("${docker_cmd[@]}" inspect -f '{{.Image}}' telegram-media-archive-archive-1)
candidate_image=$("${docker_cmd[@]}" image inspect -f '{{.Id}}' "$candidate")
test -n "$current_image" && test -n "$candidate_image"
test "$current_image" != "$candidate_image" || echo "NOTICE: candidate is same as live image"
"${docker_cmd[@]}" run --rm --network none --read-only --entrypoint python "$candidate" -c 'import app.main, app.store, app.config; print("CANDIDATE_IMPORT_OK")'
echo "LIVE_IMAGE_ID=$current_image"
echo "CANDIDATE_IMAGE_ID=$candidate_image"
echo "DEPLOYMENT_GATE_OK"
echo "NOTICE: no deployment, switch-over, or rollback was performed"
