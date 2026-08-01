import re

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, new_task, send_message, get_stats

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_stats_pattern = re.compile(rf"^[{prefixes}]stats(?:\s+.+)?$", re.IGNORECASE)

_AUTHORIZED_IDS = {config.OWNER_ID, config.ADMIN_ID}


def _is_authorized(user_id: int) -> bool:
    return user_id in _AUTHORIZED_IDS


@ItsMrULPBot.on(events.NewMessage(pattern=_stats_pattern))
@new_task
async def stats_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    
    stats = get_stats()
    
    text = (
        "**🔍 Bot Statistics 📋**\n"
        "**━━━━━━━━━━━━━━━━**\n"
        f"**Total ULP Searches** : `{stats.get('total_ulp_searches', 0)}`\n"
        f"**Total Extract Searches** : `{stats.get('total_extract_searches', 0)}`\n"
        f"**Total Combo Searches** : `{stats.get('total_combo_searches', 0)}`\n"
        f"**Total Users Tracked** : `{stats.get('total_users', 0)}`\n"
        f"**━━━━━━━━━━━━━━━━**\n"
        f"**Total Searches** : `{sum([stats.get('total_ulp_searches', 0), stats.get('total_extract_searches', 0), stats.get('total_combo_searches', 0)])}`"
    )
    
    await send_message(chat_id, text)
    LOGGER.info(f"Stats command | User: {sender.id}")
