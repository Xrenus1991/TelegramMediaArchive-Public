"""Pure media classification, accessible independently of Telegram network."""
from pathlib import Path


def kind_of(message):
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "gif", None):
        return "animation"
    if message.document and (getattr(message.document, "mime_type", "") or "").startswith("image/"):
        return "image"
    if message.document:
        return "document"
    return None


def restricted_reason(message):
    if getattr(message, "noforwards", False):
        return "protected content"
    media = getattr(message, "media", None)
    ttl = getattr(media, "ttl_seconds", None)
    if ttl is not None and ttl > 0:
        return "view-once / self-destructing media"
    return None


def safe_archive_path(root: Path, relative_path: str) -> Path | None:
    """Reject absolute paths or traversal, even if DB is accidentally modified."""
    root = root.resolve()
    proposed = (root / relative_path).resolve()
    return proposed if proposed.is_relative_to(root) and proposed.is_file() else None

