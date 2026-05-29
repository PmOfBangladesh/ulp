import asyncio
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from telethon import Button, events

import config
from bot import ItsMrULPBot
from helpers import (
    LOGGER,
    SmartButtons,
    delete_messages,
    edit_message,
    new_task,
    send_message,
)
from helpers.botutils import get_args_str
from helpers.func import scan_db_for_mixed_combos, log_user_extraction

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_summary_pattern = re.compile(rf"^[{prefixes}]summary$", re.IGNORECASE)

_LINES_PER_PAGE: int = 38
_sessions: Dict[int, Dict] = {}


def _categorize_combos(combos: List[Tuple[str, str, str]]) -> Dict[str, int]:
    """Count combos by type."""
    counts = {
        "email": 0,
        "user": 0,
        "phone": 0,
        "url_email": 0,
        "url_user": 0,
        "url_phone": 0,
    }
    for ctype, _, _ in combos:
        counts[ctype] = counts.get(ctype, 0) + 1
    return counts


def _extract_domain(identifier: str) -> str:
    """Extract domain from identifier (URL or email)."""
    if identifier.startswith(('http://', 'https://', 'ftp://')):
        # Extract domain from URL
        match = re.match(r'https?://(?:www\.)?([^/:?#]+)', identifier)
        if match:
            return match.group(1)
    elif '@' in identifier:
        # Extract domain from email
        return identifier.split('@')[1]
    # For usernames or phone numbers, just return the identifier
    return identifier


def _group_combos_by_site(combos: List[Tuple[str, str, str]]) -> Dict[str, List[Tuple[str, str, str]]]:
    """Group combos by site/domain."""
    sites = defaultdict(list)
    for combo in combos:
        ctype, ident, pwd = combo
        domain = _extract_domain(ident)
        sites[domain].append(combo)
    return dict(sites)


def _build_full_summary(combos: List[Tuple[str, str, str]], elapsed_ms: float) -> str:
    """Build full summary showing site breakdown."""
    sites = _group_combos_by_site(combos)
    counts = _categorize_combos(combos)
    
    # Sort sites by count descending
    sorted_sites = sorted(sites.items(), key=lambda x: len(x[1]), reverse=True)
    
    lines = [
        "**🔍 FULL DATABASE SUMMARY 📊**",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        f"**Total Records**: `{len(combos)}`",
        f"**📧 Email:Pass**: `{counts['email']}`",
        f"**👤 User:Pass**: `{counts['user']}`",
        f"**📱 Phone:Pass**: `{counts['phone']}`",
        f"**🌐📧 URL+Email**: `{counts['url_email']}`",
        f"**🌐👤 URL+User**: `{counts['url_user']}`",
        f"**🌐📱 URL+Phone**: `{counts['url_phone']}`",
        f"**⏱️ Scan Time**: `{elapsed_ms}ms`",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        "",
        "**📍 SITE BREAKDOWN:**",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
    ]
    
    # Add site information
    for i, (site, site_combos) in enumerate(sorted_sites, 1):
        lines.append(f"**{i}. {site} : Line {len(site_combos)}**")
    
    lines.extend([
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        "",
        "**📋 RESULTS:**",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
    ])
    
    # Add all combos
    for i, (ctype, ident, pwd) in enumerate(combos, 1):
        ident_display = ident[:50] + "..." if len(ident) > 50 else ident
        pwd_display = pwd[:30] + "..." if len(pwd) > 30 else pwd
        
        type_emoji = {
            "email": "📧",
            "user": "👤",
            "phone": "📱",
            "url_email": "🌐📧",
            "url_user": "🌐👤",
            "url_phone": "🌐📱",
        }.get(ctype, "📝")
        
        lines.append(f"**{i}. {type_emoji} {ident_display}:{pwd_display}**")
    
    lines.append("**━━━━━━━━━━━━━━━━━━━━━━━━━━━**")
    
    return "\n".join(lines)


def _build_summary_page(combos: List[Tuple[str, str, str]], page: int) -> str:
    """Build a summary page with combos."""
    start = page * _LINES_PER_PAGE
    end = start + _LINES_PER_PAGE
    page_combos = combos[start:end]

    lines = [
        "**🔍 DATABASE SUMMARY 📋**",
        "**━━━━━━━━━━━━━━━━━━━━━**",
    ]

    for i, (ctype, ident, pwd) in enumerate(page_combos, start=start + 1):
        # Truncate long lines for readability
        ident_display = ident[:30] + "..." if len(ident) > 30 else ident
        pwd_display = pwd[:20] + "..." if len(pwd) > 20 else pwd
        
        type_emoji = {
            "email": "📧",
            "user": "👤",
            "phone": "📱",
            "url_email": "🌐📧",
            "url_user": "🌐👤",
            "url_phone": "🌐📱",
        }.get(ctype, "📝")

        lines.append(f"**{i}. {type_emoji} {ident_display}:{pwd_display}**")

    lines.extend([
        "**━━━━━━━━━━━━━━━━━━━━━**",
        f"**Page {page + 1}** | **👁 Use Buttons To Navigate ✅**"
    ])

    return "\n".join(lines)


def _build_summary_header(combos: List[Tuple[str, str, str]], elapsed_ms: float) -> str:
    """Build summary header with statistics."""
    counts = _categorize_combos(combos)
    total = len(combos)
    
    lines = [
        "**🔍 DATABASE SUMMARY ANALYSIS 📊**",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        f"**Total Records**: `{total}`",
        f"**📧 Email:Pass**: `{counts['email']}`",
        f"**👤 User:Pass**: `{counts['user']}`",
        f"**📱 Phone:Pass**: `{counts['phone']}`",
        f"**🌐📧 URL+Email**: `{counts['url_email']}`",
        f"**🌐👤 URL+User**: `{counts['url_user']}`",
        f"**🌐📱 URL+Phone**: `{counts['url_phone']}`",
        f"**⏱️ Scan Time**: `{elapsed_ms}ms`",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        "**Starting Summary...**",
    ]
    
    return "\n".join(lines)


def _nav_buttons(page: int, total: int, cid: int):
    """Create pagination buttons with Full Summary option."""
    row = []
    if page > 0:
        row.append(Button.inline("◀️ Previous", data=f"sumpg:prev:{cid}:{page}".encode()))
    if page < total - 1:
        row.append(Button.inline("Next ➡️", data=f"sumpg:next:{cid}:{page}".encode()))
    row.append(Button.inline("📊 Full Summary", data=f"sumpg:full:{cid}".encode()))
    row.append(Button.inline("❌ Close", data=b"sumpg:close"))
    return [row] if row else None


@ItsMrULPBot.on(events.NewMessage(pattern=_summary_pattern))
@new_task
async def summary_handler(event, bot):
    """Handle /summary command - scan DB and show summary with pagination."""
    sender = await event.get_sender()
    from helpers import add_user
    add_user(sender.id)
    
    chat_id = event.chat_id
    user_id = sender.id

    msg = await send_message(chat_id, "**🔍 Scanning Database For Mixed Combos... Please Wait ⏳**")
    if not msg:
        return

    try:
        combos, removed, elapsed_ms = await scan_db_for_mixed_combos(__file__)
    except Exception as exc:
        LOGGER.error(f"summary_handler error: {exc}")
        await edit_message(chat_id, msg.id, "**❌ Error Scanning Database**")
        return

    if not combos:
        await edit_message(chat_id, msg.id, "**❌ No Mixed Combos Found In Database**")
        return

    # Log the summary scan
    log_user_extraction(user_id, None, "SUMMARY", len(combos), source="database_scan")

    # Calculate pagination
    total_pages = max(1, (len(combos) + _LINES_PER_PAGE - 1) // _LINES_PER_PAGE)
    _sessions[chat_id] = {
        "combos": combos,
        "total_pages": total_pages,
        "removed": removed,
        "elapsed_ms": elapsed_ms,
    }

    # Show header first
    header = _build_summary_header(combos, elapsed_ms)
    await edit_message(chat_id, msg.id, header)

    # Wait a moment, then show first page
    await asyncio.sleep(1)
    page_text = _build_summary_page(combos, 0)
    btns = _nav_buttons(0, total_pages, chat_id)
    await edit_message(chat_id, msg.id, page_text, buttons=btns)


@ItsMrULPBot.on(events.CallbackQuery(data=re.compile(rb"^sumpg:")))
async def summary_pagination_cb(event):
    """Handle summary pagination and full summary."""
    sender = await event.get_sender()
    raw = event.data.decode()
    parts = raw.split(":")
    action = parts[1]

    if action == "close":
        await event.edit("**❌ Summary Closed**")
        return

    if action == "full":
        chat_id = int(parts[2])
        session = _sessions.get(chat_id)
        if not session:
            await event.answer("Session expired. Run /summary again.", alert=True)
            return
        
        combos = session["combos"]
        elapsed_ms = session["elapsed_ms"]
        
        # Build and display full summary
        full_summary = _build_full_summary(combos, elapsed_ms)
        
        # Split into chunks if too long (Telegram limit is 4096 chars per message)
        chunk_size = 4000
        if len(full_summary) > chunk_size:
            # Send first chunk
            msg_id = event.query.msg_id
            await ItsMrULPBot.edit_message(chat_id, msg_id, full_summary[:chunk_size], parse_mode="markdown")
            
            # Send remaining chunks
            remaining = full_summary[chunk_size:]
            while remaining:
                chunk = remaining[:chunk_size]
                await send_message(chat_id, chunk)
                remaining = remaining[chunk_size:]
        else:
            await event.edit(full_summary, parse_mode="markdown")
        return

    if action not in ("prev", "next"):
        await event.answer("Unknown action", alert=True)
        return

    chat_id = int(parts[2])
    cur_page = int(parts[3])
    
    session = _sessions.get(chat_id)
    if not session:
        await event.answer("Session expired. Run /summary again.", alert=True)
        return

    combos = session["combos"]
    total_pages = session["total_pages"]
    
    # Calculate new page
    if action == "next":
        new_page = min(cur_page + 1, total_pages - 1)
    else:
        new_page = max(cur_page - 1, 0)

    page_text = _build_summary_page(combos, new_page)
    btns = _nav_buttons(new_page, total_pages, chat_id)
    msg_id = event.query.msg_id
    await ItsMrULPBot.edit_message(chat_id, msg_id, page_text, parse_mode="markdown", buttons=btns)
