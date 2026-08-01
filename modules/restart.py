import os
import re
import sys
import signal

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, new_task, send_message

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_restart_pattern = re.compile(rf"^[{prefixes}]restart$", re.IGNORECASE)
_stop_pattern = re.compile(rf"^[{prefixes}]stop$", re.IGNORECASE)

_AUTHORIZED_IDS = {config.OWNER_ID, config.ADMIN_ID}


def _is_authorized(user_id: int) -> bool:
    return user_id in _AUTHORIZED_IDS


@ItsMrULPBot.on(events.NewMessage(pattern=_restart_pattern))
@new_task
async def restart_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    await send_message(chat_id, "**🔄 Restarting Bot...⏳**")
    LOGGER.info(f"Restart command issued by user {sender.id}")
    
    # Restart the bot process
    os.execvp(sys.executable, [sys.executable] + sys.argv)


@ItsMrULPBot.on(events.NewMessage(pattern=_stop_pattern))
@new_task
async def stop_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    await send_message(chat_id, "**⛔ Stopping Bot...⏳**")
    LOGGER.info(f"Stop command issued by user {sender.id}")
    
    # Stop the bot gracefully
    await ItsMrULPBot.disconnect()
    sys.exit(0)
