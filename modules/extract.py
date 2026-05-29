import asyncio
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from telethon import events
from telethon.tl.types import Message

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
from helpers.func import (
    ACCEPTED_FORMAT_KEYS,
    run_extract_on_lines,
    run_extract_on_datastore,
    get_file_size_str,
    write_result_file,
    read_lines_from_file,
    log_user_extraction,
)

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_extract_cmd = re.compile(rf"^[{prefixes}]extract(?:\s+.*)?$", re.IGNORECASE)

_FORMAT_LABELS: Dict[str, str] = {
    "mailpass": "📥 Mail Pass",
    "userpass": "📥 User Pass",
    "num_pass": "📥 Number Pass",
    "domain":   "📥 Domain",
    "url":      "📥 URL",
}

_pending_sessions: Dict[int, Dict] = {}


def _build_format_picker() -> object:
    sb = SmartButtons()
    sb.button("📥 Mail Pass",    callback_data="exfmt:mailpass")
    sb.button("📥 User Pass",    callback_data="exfmt:userpass")
    sb.button("📥 Number Pass",  callback_data="exfmt:num_pass")
    sb.button("📥 Domain",       callback_data="exfmt:domain")
    sb.button("📥 URL",          callback_data="exfmt:url")
    sb.button("❌ Cancel",       callback_data="exfmt:cancel")
    return sb.build_menu(b_cols=2)


def _build_channel_button() -> object:
    sb = SmartButtons()
    sb.button("Updates Channel 🇧🇩", url=f"https://{config.UPDATE_CHANNEL_URL}")
    return sb.build_menu(b_cols=1)


async def _do_extraction(
    chat_id: int,
    user_id: int,
    status_msg: Message,
    keyword: Optional[str],
    fmt_key: str,
    source_file_path: Optional[str],
    caller_file: str,
) -> None:
    try:
        if source_file_path:
            source_lines = await read_lines_from_file(source_file_path)
            if not source_lines:
                await edit_message(chat_id, status_msg.id, "**❌ Replied File Has No Valid Lines**")
                return
            matched_lines, dupes_removed, elapsed_ms = await run_extract_on_lines(
                source_lines, fmt_key
            )
            source_type = "file"
        else:
            matched_lines, dupes_removed, elapsed_ms = await run_extract_on_datastore(
                keyword, fmt_key, caller_file
            )
            source_type = "keyword"
    except Exception as exc:
        LOGGER.error(f"_do_extraction error: {exc}")
        await edit_message(chat_id, status_msg.id, "**❌ Something Went Wrong During Processing**")
        return

    if not matched_lines:
        await edit_message(chat_id, status_msg.id, "**❌ Sorry No Results Found In Database**")
        return

    # Log user extraction
    log_user_extraction(user_id, keyword, fmt_key, len(matched_lines), source=source_type)

    await edit_message(chat_id, status_msg.id, "**Found ☑️ Processing...**")

    label = keyword if keyword else (Path(source_file_path).stem if source_file_path else fmt_key)
    try:
        file_path = await asyncio.get_running_loop().run_in_executor(
            None, write_result_file, f"EXTRACT_{fmt_key.upper()}", label, matched_lines
        )
    except Exception as exc:
        LOGGER.error(f"_do_extraction write error: {exc}")
        await edit_message(chat_id, status_msg.id, "**❌ Failed To Write Output File**")
        return

    fname = Path(file_path).name
    fsize = get_file_size_str(file_path)
    fmt_label = _FORMAT_LABELS.get(fmt_key, fmt_key.upper())
    last_upd = [time.time()]
    t_start = time.time()

    async def _on_progress(cur, tot):
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
            f"**Matched Lines** : `{len(matched_lines)}`\n"
            f"**Duplicates Removed** : `{dupes_removed}`\n"
            f"**Time Taken** : `{elapsed_ms}ms`\n"
            f"**━━━━━━━━━━━━━━━━**\n"
            f"**Thanks For Using Smart Service 📥**"
        ),
        force_document=True,
        buttons=_build_channel_button(),
        progress_callback=_on_progress,
    )

    await delete_messages(chat_id, status_msg.id)
    clean_download(file_path)


@ItsMrULPBot.on(events.NewMessage(pattern=_extract_cmd))
@new_task
async def extract_command_handler(event, bot):
    chat_id = event.chat_id
    sender = await event.get_sender()
    from helpers import add_user
    add_user(sender.id)
    
    keyword = get_args_str(event).strip() or None
    replied_file_path: Optional[str] = None

    reply = await event.get_reply_message()
    if reply and reply.document:
        fname = reply.file.name or ""
        if fname.lower().endswith(".txt"):
            dl_dir = Path(__file__).resolve().parent.parent / "downloads"
            dl_dir.mkdir(exist_ok=True)
            dest = str(dl_dir / f"extr_input_{chat_id}_{int(time.time())}.txt")
            try:
                await reply.download_media(file=dest)
                replied_file_path = dest
            except Exception as exc:
                LOGGER.error(f"extract download replied file error: {exc}")
        else:
            await send_message(chat_id, "**❌ Only .txt Files Are Supported**")
            return

    if replied_file_path is None and keyword is None:
        await send_message(
            chat_id,
            "**❌ Reply To A .txt File Or Provide A Keyword**\n"
            "**━━━━━━━━━━━━━━━━**\n"
            "**Usage 1:** Reply to a `.txt` file and send `/extract`\n"
            "**Usage 2:** `/extract <keyword>`\n"
            "**━━━━━━━━━━━━━━━━**\n"
            "**Example:** `/extract facebook`",
        )
        return

    _pending_sessions[chat_id] = {
        "keyword": keyword,
        "file_path": replied_file_path,
        "caller": __file__,
    }

    picker_msg = await send_message(chat_id, "**🔍 Please Choose Output Format 📥**")
    if picker_msg:
        _pending_sessions[chat_id]["picker_msg_id"] = picker_msg.id
        await edit_message(chat_id, picker_msg.id, "**🔍 Please Choose Output Format 📥**", buttons=_build_format_picker())


@ItsMrULPBot.on(events.CallbackQuery(data=re.compile(rb"^exfmt:")))
async def extract_format_callback(event):
    chat_id = event.chat_id
    sender = await event.get_sender()
    user_id = sender.id
    raw_data = event.data.decode("utf-8", errors="replace")
    fmt_key = raw_data.split(":", 1)[1]

    session = _pending_sessions.pop(chat_id, None)

    if fmt_key == "cancel":
        if session and session.get("file_path"):
            clean_download(session["file_path"])
        await event.edit("**❌ Extraction Cancelled**")
        return

    if fmt_key not in ACCEPTED_FORMAT_KEYS:
        await event.edit("**❌ Unknown Format Selected**")
        return

    if session is None:
        await event.edit("**❌ Session Expired — Please Run The Command Again**")
        return

    keyword = session.get("keyword")
    file_path = session.get("file_path")
    caller = session.get("caller", __file__)

    await event.edit(f"**Searching Whole Database For Keyword 🔍**")

    status_msg = await event.get_message()

    await _do_extraction(
        chat_id=chat_id,
        user_id=user_id,
        status_msg=status_msg,
        keyword=keyword,
        fmt_key=fmt_key,
        source_file_path=file_path,
        caller_file=caller,
    )
