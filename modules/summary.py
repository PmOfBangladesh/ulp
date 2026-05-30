import asyncio
import os
import re
import time
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
from helpers.func import scan_db_for_mixed_combos, log_user_extraction, collect_datastore_paths

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_summary_pattern = re.compile(rf"^[{prefixes}]summary$", re.IGNORECASE)

_LINES_PER_PAGE: int = 38
_sessions: Dict[int, Dict] = {}


def _get_db_stats(caller_file: str = None) -> Tuple[int, str, str]:
    """Get total lines, size, and last update from database files."""
    try:
        # Use collect_datastore_paths if caller_file provided, else use default data dir
        if caller_file:
            from utils.engine import collect_datastore_paths
            paths = collect_datastore_paths(caller_file)
        else:
            paths = list(Path("/tmp/workspace/PmOfBangladesh/ulp/data").glob("*.txt"))
        
        if not paths:
            return 0, "0 B", "Unknown"
        
        total_lines = 0
        total_size = 0
        latest_mtime = 0
        
        for path in paths:
            try:
                # Count lines
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    total_lines += sum(1 for _ in f)
                
                # Get size
                total_size += os.path.getsize(path)
                
                # Get modification time
                mtime = os.path.getmtime(path)
                latest_mtime = max(latest_mtime, mtime)
            except Exception as e:
                LOGGER.error(f"Error reading {path}: {e}")
                continue
        
        # Format size
        if total_size < 1024:
            size_str = f"{total_size} B"
        elif total_size < 1024 * 1024:
            size_str = f"{total_size / 1024:.2f} KB"
        elif total_size < 1024 * 1024 * 1024:
            size_str = f"{total_size / (1024 * 1024):.2f} MB"
        else:
            size_str = f"{total_size / (1024 * 1024 * 1024):.2f} GB"
        
        # Format last update time
        if latest_mtime:
            diff = time.time() - latest_mtime
            hours = int(diff / 3600)
            if hours < 1:
                update_str = "Less than 1 hour ago"
            elif hours == 1:
                update_str = "1 hour ago"
            elif hours < 24:
                update_str = f"{hours} hours ago"
            else:
                days = hours // 24
                if days == 1:
                    update_str = "1 day ago"
                else:
                    update_str = f"{days} days ago"
        else:
            update_str = "Unknown"
        
        return total_lines, size_str, update_str
    except Exception as e:
        LOGGER.error(f"Error getting DB stats: {e}")
        return 0, "0 B", "Unknown"


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


def _build_top_20_domains(combos: List[Tuple[str, str, str]], caller_file: str = None) -> str:
    """Build top 20 domains summary."""
    # Group by domain
    sites = defaultdict(int)
    for ctype, ident, pwd in combos:
        domain = _extract_domain(ident)
        sites[domain] += 1
    
    # Sort by count descending and get top 20
    sorted_sites = sorted(sites.items(), key=lambda x: x[1], reverse=True)[:20]
    
    # Get database stats
    total_lines, size_str, update_str = _get_db_stats(caller_file)
    
    lines = [
        "**🌐 Top 20 Domains**",
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
    ]
    
    # Add top 20 domains
    for i, (domain, count) in enumerate(sorted_sites, 1):
        # Format count with commas
        count_str = f"{count:,}"
        lines.append(f"**{i}. {domain} — {count_str}**")
    
    lines.extend([
        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
        f"**Total Lines: {total_lines:,}**",
        f"**Total Size: {size_str}**",
        f"**Last Update: {update_str}**",
    ])
    
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
    """Create pagination buttons with Top 20 Domains option."""
    row = []
    if page > 0:
        row.append(Button.inline("◀️ Previous", data=f"sumpg:prev:{cid}:{page}".encode()))
    if page < total - 1:
        row.append(Button.inline("Next ➡️", data=f"sumpg:next:{cid}:{page}".encode()))
    row.append(Button.inline("🌐 Top 20 Domains", data=f"sumpg:full:{cid}".encode()))
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
    """Handle summary pagination and top 20 domains display."""
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
        
        # Build and display top 20 domains
        top_20 = _build_top_20_domains(combos, __file__)
        await event.edit(top_20, parse_mode="markdown")
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
