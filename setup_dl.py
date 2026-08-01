#!/usr/bin/env python3
"""
One‑time setup: log into a Telegram **user** account and save the session.

Run this **once** from the terminal on the same machine where the bot runs.
After successful login the session file ``dl_session.session`` is created
and the ``/dl`` bot command will work without any further login prompts.

Usage
-----
    cd /root/ulp
    python3 setup_dl.py

The script uses the same ``API_ID`` / ``API_HASH`` as ``config.py``.
"""

import asyncio
import sys
import os

# make sure we can import config even when run from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError,
)

import config

SESSION_NAME = "dl_session"


async def main():
    print("=" * 50)
    print("  Aliya Ulp – User Session Setup")
    print("=" * 50)
    print()

    # check if session already exists
    if os.path.exists(f"{SESSION_NAME}.session"):
        print("⚠️  Existing session file found.")
        overwrite = input("Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("👉 Keeping existing session. Run the bot and use /dl.")
            return
        os.remove(f"{SESSION_NAME}.session")
        print("🗑  Old session removed.\n")

    client = TelegramClient(SESSION_NAME, config.API_ID, config.API_HASH)

    await client.connect()

    if await client.is_user_authorized():
        print("✅ Already authorised (cached). No login needed.")
        await client.disconnect()
        return

    # ---- phone ----
    phone = input("📱 Phone number (with country code, e.g. +8801XXXXXXXXX): ").strip()
    try:
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print("❌ Invalid phone number.")
        await client.disconnect()
        return
    except FloodWaitError as fw:
        print(f"⏳ Flood wait: {fw.seconds} seconds. Try later.")
        await client.disconnect()
        return
    except Exception as exc:
        print(f"❌ Error: {exc}")
        await client.disconnect()
        return

    # ---- OTP ----
    code = input("📩 Enter the verification code: ").strip()

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        # ---- 2FA ----
        password = input("🔒 2FA enabled. Enter your password: ").strip()
        try:
            await client.sign_in(password=password)
        except Exception as exc:
            print(f"❌ 2FA failed: {exc}")
            await client.disconnect()
            return
    except PhoneCodeInvalidError:
        print("❌ Invalid code.")
        await client.disconnect()
        return
    except Exception as exc:
        print(f"❌ Login error: {exc}")
        await client.disconnect()
        return

    print()
    print("✅ Login successful!")
    print(f"📁 Session saved to  {SESSION_NAME}.session")
    print()
    print("👉  You can now use  /dl  in the bot to download media from channels.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
