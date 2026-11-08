import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    data_dir: Path
    max_file_mb: int
    min_free_mb: int
    bot_delivery: bool
    bot_send_max_mb: int
    auto_group_archive: bool


def load_config() -> Config:
    required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "CONTROL_BOT_TOKEN")
    absent = [k for k in required if not os.getenv(k)]
    if absent:
        raise ValueError("Missing required environment variables: " + ", ".join(absent))
    api_id = int(os.environ["TELEGRAM_API_ID"])
    owner_id = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
    max_file_mb = int(os.getenv("MAX_FILE_MB", "300"))
    min_free_mb = int(os.getenv("MIN_FREE_MB", "1024"))
    bot_send_max_mb = int(os.getenv("BOT_SEND_MAX_MB", "45"))
    if api_id <= 0 or owner_id < 0 or not (1 <= max_file_mb <= 2048):
        raise ValueError("Invalid API ID, owner ID or MAX_FILE_MB")
    if min_free_mb < 100 or not (1 <= bot_send_max_mb <= 200):
        raise ValueError("Invalid MIN_FREE_MB or BOT_SEND_MAX_MB")
    return Config(
        api_id=api_id,
        api_hash=os.environ["TELEGRAM_API_HASH"],
        bot_token=os.environ["CONTROL_BOT_TOKEN"],
        owner_id=owner_id,
        data_dir=Path(os.getenv("DATA_DIR", "/data")).resolve(),
        max_file_mb=max_file_mb,
        min_free_mb=min_free_mb,
        bot_delivery=os.getenv("ENABLE_BOT_DELIVERY", "false").lower() in ("true", "yes", "1"),
        bot_send_max_mb=bot_send_max_mb,
        auto_group_archive=os.getenv("AUTO_ARCHIVE_GROUPS", "false").lower() in ("true", "yes", "1"),
    )
