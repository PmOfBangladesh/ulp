import asyncio
import re
import time
from pathlib import Path
from typing import Dict, Optional

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import (
    LOGGER,
    SmartButtons,
    clean_download,
    delete_messages,
    edit_message,
    new_task,
    progress_bar,
    send_file,
    send_message,
)
from helpers.botutils import get_args_str
from helpers.func import run_combo_search, get_file_size_str, write_result_file, log_user_extraction, notify_combo_extraction

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_cmb_pattern = re.compile(rf"^[{prefixes}]cmb(?:\s+.*)?$", re.IGNORECASE)

_CMB_FORMATS = {
    "mailpass": "📥 Mail Pass",
    "userpass": "📥 User Pass",
    "num_pass": "📥 Number Pass",
}

_cmb_sessions: Dict[int, Dict] = {}


def _build_picker():
    sb = SmartButtons()
    sb.button("📥 Mail Pass",   callback_data="cmbfmt:mailpass")
    sb.button("📥 User Pass",   callback_data="cmbfmt:userpass")
    sb.button("📥 Number Pass", callback_data="cmbfmt:num_pass")
    sb.button("❌ Cancel",      callback_data="cmbfmt:cancel")
    return sb.build_menu(b_cols=2)


def _build_channel_button():
    sb = SmartButtons()
    sb.button("Updates Channel 🇧🇩", url=f"https://{config.UPDATE_CHANNEL_URL}")
    return sb.build_menu(b_cols=1)


@ItsMrULPBot.on(events.NewMessage(pattern=_cmb_pattern))
@new_task
async def cmb_handler(event, bot):
    keyword = get_args_str(event).strip()
    chat_id = event.chat_id
    sender = await event.get_sender()
    
    from helpers import add_user
    add_user(sender.id)

    if not keyword:
        await send_message(chat_id, "**❌ Please Provide Keyword After The Command**")
        return

    _cmb_sessions[chat_id] = {"keyword": keyword}

    msg = await send_message(chat_id, "**🔍 Please Choose Output Format 📥**")
    if not msg:
        return
    _cmb_sessions[chat_id]["picker_msg_id"] = msg.id
    await edit_message(chat_id, msg.id, "**🔍 Please Choose Output Format 📥**", buttons=_build_picker())


@ItsMrULPBot.on(events.CallbackQuery(data=re.compile(rb"^cmbfmt:")))
async def cmb_format_cb(event):
    chat_id = event.chat_id
    sender = await event.get_sender()
    user_id = sender.id
    fmt_key = event.data.decode().split(":", 1)[1]
    session = _cmb_sessions.pop(chat_id, None)

    if fmt_key == "cancel":
        await event.edit("**❌ Cancelled**")
        return

    if fmt_key not in _CMB_FORMATS:
        await event.edit("**❌ Unknown Format**")
        return

    if session is None:
        await event.edit("**❌ Session Expired — Run The Command Again**")
        return

    keyword = session["keyword"]
    await event.edit("**Searching Whole Database For Keyword 🔍**")
    status_msg = await event.get_message()

    try:
        matched, dupes, elapsed_ms = await run_combo_search(keyword, __file__)
    except Exception as exc:
        LOGGER.error(f"cmb_format_cb error: {exc}")
        await event.edit("**❌ Sorry Database Empty**")
        return

    if not matched:
        await event.edit("**❌ Sorry Database Empty**")
        return

    # Log user combo search
    log_user_extraction(user_id, keyword, fmt_key, len(matched), source="keyword")
    
    # Notify owner about combo extraction
    await notify_combo_extraction(user_id, keyword, fmt_key, len(matched))

    await event.edit("**Found ☑️ Processing...**")

    try:
        file_path = await asyncio.get_running_loop().run_in_executor(
            None, write_result_file, f"CMB_{fmt_key.upper()}", keyword, matched
        )
    except Exception as exc:
        LOGGER.error(f"cmb write error: {exc}")
        await event.edit("**❌ Failed To Write Output File**")
        return

    fname = Path(file_path).name
    fsize = get_file_size_str(file_path)
    fmt_label = _CMB_FORMATS[fmt_key]
    last_upd = [time.time()]
    t_start = time.time()

    async def _prog(cur, tot):
        await progress_bar(cur, tot, status_msg, t_start, last_upd)

    await send_file(
        chat_id,
        file_path,
        caption=(
            f"**🔍 Showing Processed File's Info 📋**\n"
            f"**━━━━━━━━━━━━━━━━**\n"
            f"**File Name** : `{fname}`\n"
            f"**File Size** : `{fsize}`\n"
            f"**File Format** : `{fmt_label}`\n"
            f"**Matched Lines** : `{len(matched)}`\n"
            f"**Duplicates Removed** : `{dupes}`\n"
            f"**Time Taken** : `{elapsed_ms}ms`\n"
            f"**━━━━━━━━━━━━━━━━**\n"
            f"**Thanks For Using Smart Service 📥**"
        ),
        force_document=True,
        buttons=_build_channel_button(),
        progress_callback=_prog,
    )

    await delete_messages(chat_id, status_msg.id)
    clean_download(file_path)