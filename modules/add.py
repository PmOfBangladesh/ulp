import asyncio
import re
import time
from pathlib import Path
from typing import Dict, List

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, edit_message, new_task, send_message, get_all_users, increment_stat

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
add_pattern = re.compile(rf"^[{prefixes}]add(?:\s+.*)?$", re.IGNORECASE)

_AUTHORIZED_IDS = {config.OWNER_ID, config.ADMIN_ID}

_add_sessions: Dict[int, Dict] = {}


def _is_authorized(user_id: int) -> bool:
    return user_id in _AUTHORIZED_IDS


async def _notify_users_new_ulp(file_count: int):
    """Notify all users about new ULP data added."""
    users = get_all_users()
    if not users:
        return
    
    increment_stat("total_ulp_additions", 1)
    
    message = (
        f"**🎉 New Fresh ULP Line Added! 📥**\n"
        f"**━━━━━━━━━━━━━━━━**\n"
        f"**New Database Files** : `{file_count}`\n"
        f"**━━━━━━━━━━━━━━━━**\n"
        f"**Try using /ulp command to search!**"
    )
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            await ItsMrULPBot.send_message(user_id, message)
            success += 1
        except Exception as exc:
            LOGGER.error(f"Failed to notify user {user_id}: {exc}")
            failed += 1
        
        # Rate limiting
        if (success + failed) % 5 == 0:
            await asyncio.sleep(0.05)
    
    LOGGER.info(f"Notified {success} users about new ULP data (Failed: {failed})")


@ItsMrULPBot.on(events.NewMessage(pattern=add_pattern))
@new_task
async def add_command_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    from helpers.botutils import get_args_str
    raw = get_args_str(event).strip()

    if not raw:
        await send_message(chat_id, "**❌ Please Provide Amount After Command**")
        return

    if not raw.isdigit() or int(raw) < 1:
        await send_message(chat_id, "**❌ Please Provide A Valid Number**")
        return

    amount = int(raw)

    _add_sessions[chat_id] = {
        "expected": amount,
        "received": [],
        "owner_id": sender.id,
    }

    await send_message(chat_id, f"**Send The {amount} Database Files Now**")


@ItsMrULPBot.on(events.NewMessage())
@new_task
async def add_file_receiver(event, bot):
    chat_id = event.chat_id
    sender = await event.get_sender()

    session = _add_sessions.get(chat_id)
    if session is None:
        return
    if sender.id != session["owner_id"]:
        return
    if not event.document:
        return

    fname = event.file.name or ""
    if not fname.lower().endswith(".txt"):
        return

    session["received"].append(event)

    if len(session["received"]) < session["expected"]:
        return

    collected_events: List = session["received"][: session["expected"]]
    _add_sessions.pop(chat_id, None)

    confirm_msg = await send_message(chat_id, "**Thanks Successfully Received**")
    if not confirm_msg:
        return

    await asyncio.sleep(2)
    await edit_message(chat_id, confirm_msg.id, "**Downloading Them Into Server 📥**")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    failed = 0
    saved_count = 0
    for file_event in collected_events:
        try:
            safe_name = re.sub(r'[^\w\-.]', '_', file_event.file.name or "db.txt")
            dest = str(data_dir / safe_name)
            base = Path(dest).stem
            ext = Path(dest).suffix
            counter = 1
            while Path(dest).exists():
                dest = str(data_dir / f"{base}_{counter}{ext}")
                counter += 1
            await file_event.download_media(file=dest)
            LOGGER.info(f"Database file saved: {Path(dest).name}")
            saved_count += 1
        except Exception as exc:
            LOGGER.error(f"add_file_receiver download error: {exc}")
            failed += 1

    success = len(collected_events) - failed
    if failed == 0:
        await edit_message(chat_id, confirm_msg.id, "**Successfully Filed Databases With Them**")
    else:
        await edit_message(
            chat_id,
            confirm_msg.id,
            f"**Done — {success} Saved, {failed} Failed**",
        )
    
    # Notify all users about new ULP data
    if saved_count > 0:
        await asyncio.sleep(1)
        await _notify_users_new_ulp(saved_count)

