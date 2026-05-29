import asyncio
import re
import time

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, edit_message, new_task, send_message, get_all_users

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_broadcast_pattern = re.compile(rf"^[{prefixes}]broadcast(?:\s+.+)?$", re.IGNORECASE)

_AUTHORIZED_IDS = {config.OWNER_ID, config.ADMIN_ID}


def _is_authorized(user_id: int) -> bool:
    return user_id in _AUTHORIZED_IDS


@ItsMrULPBot.on(events.NewMessage(pattern=_broadcast_pattern))
@new_task
async def broadcast_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    reply = await event.get_reply_message()
    
    if not reply:
        await send_message(
            chat_id,
            "**❌ Please Reply To A Message To Broadcast**\n"
            "**━━━━━━━━━━━━━━━━**\n"
            "**Usage:** Reply to a message and send `/broadcast`"
        )
        return
    
    status_msg = await send_message(chat_id, "**Preparing To Broadcast Message...**")
    if not status_msg:
        return
    
    users = get_all_users()
    if not users:
        await edit_message(chat_id, status_msg.id, "**❌ No Users To Broadcast To**")
        return
    
    await edit_message(chat_id, status_msg.id, f"**Broadcasting To {len(users)} Users...**")
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            await ItsMrULPBot.forward_messages(
                entity=user_id,
                messages=[reply.id],
                from_peer=reply.chat_id
            )
            success += 1
        except Exception as exc:
            LOGGER.error(f"Failed to broadcast to user {user_id}: {exc}")
            failed += 1
        
        # Rate limiting
        if (success + failed) % 10 == 0:
            await asyncio.sleep(0.1)
    
    result_msg = (
        f"**🔍 Broadcast Completed 📋**\n"
        f"**━━━━━━━━━━━━━━━━**\n"
        f"**Total Users** : `{len(users)}`\n"
        f"**Success** : `{success}`\n"
        f"**Failed** : `{failed}`\n"
        f"**━━━━━━━━━━━━━━━━**"
    )
    
    await edit_message(chat_id, status_msg.id, result_msg)

