#!/usr/bin/env python3
"""Restricted, stdin-only GitHub Actions deployment receiver.

Install in authorized_keys with a forced command. It accepts only
"deploy <40-char SHA>" and a bounded tar.gz containing application files.
No SSH shell, no secrets upload, no direct access to other Compose projects.
"""
from __future__ import annotations

import fcntl
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tempfile
from datetime import datetime, timezone

PROJECT_DIR = Path("/home/monitor/TelegramMediaArchive_v1")
RELEASE_ROOT = Path("/home/monitor/telegram-media-archive-releases")
CONTAINER = "telegram-media-archive-archive-1"
PROJECT = "telegram-media-archive"
MAX_BUNDLE = 8 * 1024 * 1024
MAX_UNPACKED = 24 * 1024 * 1024
os.umask(0o077)


def call(argv, *, capture=False, timeout=600, cwd=None):
    result = subprocess.run(
        argv, cwd=cwd, check=True, timeout=timeout,
        text=True, capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def docker_cli():
    for prefix in (["docker"], ["sudo", "-n", "docker"]):
        if subprocess.run(
            prefix + ["info"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15,
        ).returncode == 0:
            return prefix
    raise RuntimeError("DOCKER_UNAVAILABLE_NONINTERACTIVE")


def stage_bundle(sha):
    payload = sys.stdin.buffer.read(MAX_BUNDLE + 1)
    if not payload or len(payload) > MAX_BUNDLE:
        raise RuntimeError("INVALID_BUNDLE_SIZE")
    RELEASE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix=sha[:12] + "-", dir=RELEASE_ROOT))
    total = 0
    found = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            members = bundle.getmembers()
            for member in members:
                pure = PurePosixPath(member.name)
                parts = pure.parts
                if (
                    not parts or pure.is_absolute()
                    or any(p in ("..", ".") for p in parts)
                    or any("\x00" in p for p in parts)
                ):
                    raise RuntimeError("INVALID_ARCHIVE_PATH")
                valid = (
                    str(pure) in ("Dockerfile", "requirements.txt", ".dockerignore")
                    or (parts[0] == "app" and len(parts) > 1)
                    or (parts == ("app",))
                )
                if not valid or not (member.isfile() or member.isdir()):
                    raise RuntimeError("UNEXPECTED_ARCHIVE_MEMBER")
                if member.size < 0 or member.size > MAX_UNPACKED:
                    raise RuntimeError("ARCHIVE_FILE_TOO_LARGE")
                total += member.size
                if total > MAX_UNPACKED:
                    raise RuntimeError("ARCHIVE_UNPACKED_TOO_LARGE")
                target = release.joinpath(*parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if str(pure) in found:
                    raise RuntimeError("DUPLICATE_ARCHIVE_MEMBER")
                found.add(str(pure))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("ARCHIVE_EXTRACT_FAILED")
                with target.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
                target.chmod(0o600)
        if not {"Dockerfile", "requirements.txt", "app/main.py"}.issubset(found):
            raise RuntimeError("REQUIRED_APPLICATION_FILES_MISSING")
    except Exception:
        shutil.rmtree(release)
        raise
    return release


def wait_ready(docker, image_id, start, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        running = call(docker + ["inspect", "-f", "{{.State.Running}}", CONTAINER], capture=True, timeout=10)
        actual = call(docker + ["inspect", "-f", "{{.Image}}", CONTAINER], capture=True, timeout=10)
        if running != "true" or actual != image_id:
            raise RuntimeError("CONTAINER_NOT_RUNNING_EXPECTED_IMAGE")
        log_result = subprocess.run(
            docker + ["logs", "--since", start, CONTAINER],
            capture_output=True, text=True, timeout=10, check=True,
        )
        if "READY:" in (log_result.stdout + log_result.stderr):
            time.sleep(5)
            restarts = call(docker + ["inspect", "-f", "{{.RestartCount}}", CONTAINER], capture=True, timeout=10)
            if restarts == "0":
                return
            raise RuntimeError("CONTAINER_RESTARTED_AFTER_READY")
        time.sleep(3)
    raise RuntimeError("READY_MARKER_TIMEOUT")


def deploy(sha, docker, release):
    print("DEPLOY_START", sha[:12], flush=True)
    call(["bash", str(PROJECT_DIR / "scripts/preflight_on_server.sh")], timeout=60)
    current = call(docker + ["inspect", "-f", "{{.Image}}", CONTAINER], capture=True, timeout=10)
    image_name = call(docker + ["inspect", "-f", "{{.Config.Image}}", CONTAINER], capture=True, timeout=10)
    if not image_name or image_name.startswith("sha256:"):
        raise RuntimeError("UNSAFE_COMPOSE_IMAGE_NAME")
    if not image_name.startswith("telegram-media-archive-archive"):
        raise RuntimeError("UNEXPECTED_LIVE_IMAGE_NAME")

    candidate = "telegram-media-archive-candidate:" + sha[:12]
    call(docker + ["build", "--tag", candidate, "--file", "Dockerfile", str(release)], timeout=900)
    call(docker + [
        "run", "--rm", "--network", "none", "--read-only",
        "--entrypoint", "python", candidate, "-B", "-c",
        'import ast,pathlib,app.main,app.store,app.config; '
        'files=list(pathlib.Path("/srv/app").rglob("*.py")); '
        '[ast.parse(p.read_text(encoding="utf-8")) for p in files]; '
        'print("CANDIDATE_IMPORT_OK")',
    ], timeout=60)
    print("CANDIDATE_READY", flush=True)

    backup_stdout = call(
        ["bash", str(PROJECT_DIR / "scripts/backup_on_server.sh")],
        capture=True, timeout=900,
    )
    match = re.search(r"^BACKUP_CREATED=(.+)$", backup_stdout, re.MULTILINE)
    if not match:
        raise RuntimeError("BACKUP_NOT_COMPLETED")
    backup_path = match.group(1)
    call(["python3", str(PROJECT_DIR / "scripts/verify_backup.py"), backup_path], timeout=120)
    print("BACKUP_VERIFIED", flush=True)

    rollback_tag = "telegram-media-archive-rollback:" + sha[:12]
    call(docker + ["tag", current, rollback_tag], timeout=20)
    candidate_id = call(docker + ["image", "inspect", "-f", "{{.Id}}", candidate], capture=True, timeout=10)
    compose = docker + [
        "compose", "-p", PROJECT, "-f", str(PROJECT_DIR / "compose.yaml"),
        "up", "-d", "--no-build", "--no-deps", "--force-recreate", "archive",
    ]
    switched = False
    try:
        call(docker + ["tag", candidate, image_name], timeout=20)
        switched = True
        start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        call(compose, timeout=120, cwd=PROJECT_DIR)
        wait_ready(docker, candidate_id, start)
    except Exception:
        if switched:
            print("DEPLOY_FAILED_ROLLBACK_START", flush=True)
            try:
                call(docker + ["tag", current, image_name], timeout=20)
                rollback_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                call(compose, timeout=120, cwd=PROJECT_DIR)
                wait_ready(docker, current, rollback_start)
                print("ROLLBACK_OK", flush=True)
            except Exception as rollback_error:
                print("ROLLBACK_FAILED:", type(rollback_error).__name__, file=sys.stderr)
        raise
    (RELEASE_ROOT / "current.sha").write_text(sha + "\n", encoding="ascii")
    print("DEPLOY_OK", sha[:12], flush=True)


def main():
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"deploy ([0-9a-f]{40})", command)
    if not match:
        raise RuntimeError("UNAUTHORIZED_DEPLOY_COMMAND")
    RELEASE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (RELEASE_ROOT / "deploy.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sha = match.group(1)
        marker = RELEASE_ROOT / "current.sha"
        if marker.exists() and marker.read_text(encoding="ascii").strip() == sha:
            print("ALREADY_DEPLOYED", sha[:12])
            return
        release = stage_bundle(sha)
        deploy(sha, docker_cli(), release)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("DEPLOY_ERROR:", type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(1)
