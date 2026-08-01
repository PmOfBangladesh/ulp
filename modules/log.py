import asyncio
import re
from typing import Dict, List

from telethon import Button, events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, edit_message, new_task, send_message
from helpers.logger import LOG_FILE


prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_log_pattern = re.compile(rf"^[{prefixes}]log$", re.IGNORECASE)

_AUTHORIZED_IDS = {config.OWNER_ID, config.ADMIN_ID}
_PAGE_SIZE = 15  # lines per page
_sessions: Dict[int, Dict] = {}  # chat_id -> {"lines": [...], "total_pages": int}


def _is_authorized(user_id: int) -> bool:
    return user_id in _AUTHORIZED_IDS


def _load_log_lines() -> List[str]:
    """Read all lines from the log file."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _escape_html(text: str) -> str:
    """Escape text for safe HTML rendering."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _page_text(lines: List[str], page: int, total_pages: int) -> str:
    """Build the HTML page content for the given page."""
    start = page * _PAGE_SIZE
    chunk = lines[start : start + _PAGE_SIZE]

    log_html = ""
    for ln in chunk:
        escaped = _escape_html(ln.rstrip("\n"))
        log_html += f"{escaped}\n"

    header = (
        f"<b>📋 Bot Logs</b> — page <b>{page + 1}</b> of <b>{total_pages}</b>\n"
        f"<pre>{log_html}</pre>\n"
        f"<i>Use buttons below to navigate</i>"
    )
    return header


def _nav_buttons(page: int, total_pages: int, chat_id: int):
    """Build Previous / Next inline buttons."""
    row = []
    if page > 0:
        row.append(
            Button.inline("◀️ Prev", data=f"logpg:prev:{chat_id}:{page}".encode())
        )
    # page indicator in the middle
    row.append(
        Button.inline(f"📄 {page + 1}/{total_pages}", data=b"logpg:noop")
    )
    if page < total_pages - 1:
        row.append(
            Button.inline("Next ▶️", data=f"logpg:next:{chat_id}:{page}".encode())
        )
    return [row] if row else None


# ── /log command handler ──────────────────────────────────────────

@ItsMrULPBot.on(events.NewMessage(pattern=_log_pattern))
@new_task
async def log_handler(event, bot):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        return

    chat_id = event.chat_id
    loop = asyncio.get_running_loop()

    lines = await loop.run_in_executor(None, _load_log_lines)

    if not lines:
        await send_message(
            chat_id, "<b>📄 No log entries found.</b>", parse_mode="html"
        )
        return

    total_pages = max(1, (len(lines) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    _sessions[chat_id] = {"lines": lines, "total_pages": total_pages}

    LOGGER.info(f"Log command | User: {sender.id}")

    msg = await send_message(
        chat_id, "<b>📄 Loading logs...</b>", parse_mode="html"
    )
    if not msg:
        return

    btns = _nav_buttons(0, total_pages, chat_id)
    await ItsMrULPBot.edit_message(
        chat_id, msg.id, _page_text(lines, 0, total_pages),
        parse_mode="html", buttons=btns,
    )


# ── pagination callback ───────────────────────────────────────────

@ItsMrULPBot.on(events.CallbackQuery(data=re.compile(rb"^logpg:")))
async def log_nav_cb(event):
    sender = await event.get_sender()
    if not _is_authorized(sender.id):
        await event.answer("❌ Not Authorized", alert=True)
        return

    raw = event.data.decode()
    parts = raw.split(":")
    direction = parts[1]
    if direction == "noop":
        await event.answer("📄 Current page", alert=False)
        return

    chat_id = int(parts[2])
    cur_page = int(parts[3])

    session = _sessions.get(chat_id)
    if not session:
        await event.answer("⏳ Session expired. Run /log again.", alert=True)
        return

    lines = session["lines"]
    total_pages = session["total_pages"]

    new_page = cur_page + 1 if direction == "next" else cur_page - 1
    new_page = max(0, min(new_page, total_pages - 1))

    btns = _nav_buttons(new_page, total_pages, chat_id)
    msg_id = event.query.msg_id

    await ItsMrULPBot.edit_message(
        chat_id, msg_id, _page_text(lines, new_page, total_pages),
        parse_mode="html", buttons=btns,
    )
