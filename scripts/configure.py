"""Secure first-time .env setup on the host, without exposing secrets on command line."""
import getpass
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ENV = HERE / ".env"


def main():
    if ENV.exists():
        print("EXISTING_ENV_UNCHANGED: settings already exist. No credentials overwritten.")
        return
    print("Telegram Media Archive: заполняем настройки. Вводимые ключи не показываются на экране.")
    print("API ID и API HASH: https://my.telegram.org/apps")
    print("Создайте НОВОГО бота через @BotFather; не используйте бота RentWeeklySender.")
    api_id = input("TELEGRAM_API_ID (число): ").strip()
    if not api_id.isdecimal() or int(api_id) <= 0:
        raise SystemExit("Некорректный API ID; настройки не записаны")
    api_hash = getpass.getpass("TELEGRAM_API_HASH (ввод скрыт): ").strip()
    bot_token = getpass.getpass("CONTROL_BOT_TOKEN нового бота (ввод скрыт): ").strip()
    if (len(api_hash) != 32 or not all(c in "0123456789abcdefABCDEF" for c in api_hash)
            or ":" not in bot_token or len(bot_token) < 30 or any(c.isspace() for c in bot_token)):
        raise SystemExit("Некорректный формат API HASH или токена; настройки не записаны")
    content = (
        f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\n"
        f"CONTROL_BOT_TOKEN={bot_token}\nDATA_DIR=/data\n"
        "MAX_FILE_MB=300\nMIN_FREE_MB=1024\n"
        "ENABLE_BOT_DELIVERY=false\nBOT_SEND_MAX_MB=45\n"
    )
    old = os.umask(0o077)
    try:
        fd = os.open(ENV, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    finally:
        os.umask(old)
    print("CONFIG_SAVED_0600: владелец определяется по авторизованному личному Telegram.")


if __name__ == "__main__":
    main()
