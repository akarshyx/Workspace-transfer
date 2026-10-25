#!/usr/bin/env python3
"""Create the protected MTProto session file used for collectible gift purchases."""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
from pathlib import Path

from telethon import TelegramClient, errors


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_PATH = Path.home() / ".local/share/rollers-casino/gift-purchase.session"


def configured_session_path() -> Path:
    value = os.environ.get("TELEGRAM_USER_SESSION_FILE", "").strip()
    path = Path(value).expanduser() if value else DEFAULT_SESSION_PATH
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return resolved
    raise RuntimeError("Session files must be stored outside the project directory.")


async def create_session() -> None:
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured first.")

    session_path = configured_session_path()
    session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session_path.parent, 0o700)
    previous_umask = os.umask(0o077)
    client = TelegramClient(
        str(session_path),
        int(api_id),
        api_hash,
        connection_retries=1,
        request_retries=1,
        auto_reconnect=False,
        timeout=15,
    )
    try:
        await client.connect()
        if await client.is_user_authorized():
            os.chmod(session_path, 0o600)
            print("The Telegram user session is already authorized and stored with restricted permissions.")
            return

        phone = getpass.getpass("Telegram phone number (input hidden): ").strip()
        if not phone:
            raise RuntimeError("A phone number is required to authorize the Telegram user account.")
        sent_code = await client.send_code_request(phone)
        code = getpass.getpass("Telegram login code (input hidden): ").strip()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("Telegram two-step verification password (input hidden): ")
            await client.sign_in(password=password)

        os.chmod(session_path, 0o600)
        print("Telegram user authorization completed. The session value and login code were not printed.")
    finally:
        try:
            await client.disconnect()
        finally:
            os.umask(previous_umask)


def main() -> int:
    logging.getLogger("telethon").setLevel(logging.CRITICAL)
    try:
        asyncio.run(create_session())
    except KeyboardInterrupt:
        print("Telegram user authorization cancelled.")
        return 1
    except Exception as exc:
        print(f"Telegram user authorization failed ({type(exc).__name__}). No credential details were logged.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
