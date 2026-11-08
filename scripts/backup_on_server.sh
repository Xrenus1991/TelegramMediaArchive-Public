#!/usr/bin/env bash
# Online snapshot: SQLite uses its backup API; media files are copied best-effort.
# No containers are stopped or restarted. Does not claim point-in-time media consistency.
set -Eeuo pipefail
umask 077
PROJECT_DIR="${PROJECT_DIR:-/home/monitor/TelegramMediaArchive_v1}"
BACKUP_ROOT="${BACKUP_ROOT:-/home/monitor/telegram-media-archive-backups}"
CONTAINER=telegram-media-archive-archive-1
cd "$PROJECT_DIR"
bash scripts/preflight_on_server.sh

docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo -n docker); fi
"${docker_cmd[@]}" info >/dev/null 2>&1 || { echo "BACKUP_FAIL: Docker unavailable"; exit 1; }

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_ROOT/$stamp"
mkdir -m 700 "$target"
# Never follow symlinks or source credentials from the host into the snapshot.
# The volume is mounted read-only; this container cannot alter live data.
image=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$CONTAINER")
"${docker_cmd[@]}" run --rm --network none --read-only \
  --volumes-from "$CONTAINER":ro \
  --mount "type=bind,src=$target,dst=/snapshot" \
  --user 0:0 --entrypoint python "$image" -c '
import os, pathlib, shutil, sqlite3, stat, sys
src=pathlib.Path("/data")
dst=pathlib.Path("/snapshot/data")
dst.mkdir(mode=0o700)
dbs=0
files=0
for base, dirs, names in os.walk(src, followlinks=False):
    base=pathlib.Path(base)
    dirs[:]=[d for d in dirs if not (base/d).is_symlink()]
    relative=base.relative_to(src)
    (dst/relative).mkdir(parents=True, exist_ok=True)
    for name in names:
        path=base/name
        mode=path.lstat().st_mode
        if not stat.S_ISREG(mode):
            continue
        destination=dst/relative/name
        if name.endswith((".sqlite", ".sqlite3", ".db", ".session")):
            source_db=sqlite3.connect("file:"+str(path)+"?mode=ro", uri=True)
            copy_db=sqlite3.connect(str(destination))
            try:
                source_db.backup(copy_db)
                if copy_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite integrity check failed")
            finally:
                copy_db.close()
                source_db.close()
            dbs+=1
        elif not name.endswith(("-wal", "-shm", "-journal")):
            shutil.copy2(path,destination,follow_symlinks=False)
            files+=1
print("SQLITE_BACKUPS_OK="+str(dbs))
print("OTHER_FILES_COPIED="+str(files))
'
# Store host configuration alongside the volume snapshot, private to the owner.
# The helper runs as root to read the Docker volume. Restrict the saved copy
# to the invoking account so restore checks work without sudo.
# Change ownership from a short-lived root helper, since the host user
# cannot traverse the root-owned 0700 directory created by the first helper.
# Only the backup destination is mounted; live /data is not mounted here.
"${docker_cmd[@]}" run --rm --network none --read-only \
  --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --security-opt no-new-privileges \
  --mount "type=bind,src=$target,dst=/snapshot" \
  --entrypoint chown "$image" -R "$(id -u):$(id -g)" /snapshot/data
find "$target/data" -type d -exec chmod 700 {} +
find "$target/data" -type f -exec chmod 600 {} +
cp -- .env "$target/.env"
cp -- compose.yaml "$target/compose.yaml"
git rev-parse HEAD > "$target/git-commit.txt"
echo "BACKUP_CREATED=$target"
echo "NOTICE: media copied while live; not an atomic multi-file snapshot"
echo "NOTICE: backup contains sensitive sessions, secrets, and private messages"
