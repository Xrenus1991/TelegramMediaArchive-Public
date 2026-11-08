"""One-time login from interactive server terminal: creates separate session file."""
import asyncio
from telethon import TelegramClient
from .config import load_config


async def main():
    cfg = load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(cfg.data_dir / "user"), cfg.api_id, cfg.api_hash)
    try:
        print("Авторизация личного Telegram. Код вводится только здесь; не отправляйте его в чат.")
        await client.start()
        me = await client.get_me()
        print("LOGIN_OK: user_id=", me.id)
        print("Держите файл сессии в секрете: он даёт доступ к Telegram-аккаунту.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
