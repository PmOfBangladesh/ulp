import asyncio
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import (
    LOGGER,
    edit_message,
    new_task,
    send_message,
)
from helpers.func import log_user_extraction
from utils.engine import collect_datastore_paths

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
_summary_pattern = re.compile(rf"^[{prefixes}]summary$", re.IGNORECASE)


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


def _get_db_stats() -> Tuple[int, str, str]:
    """Get total lines, total size, and free disk space."""
    try:
        paths = list(Path("/tmp/workspace/PmOfBangladesh/ulp/data").glob("*.txt"))
        
        if not paths:
            return 0, "0 B", "0 B"
        
        total_lines = 0
        total_size = 0
        
        for path in paths:
            try:
                # Count lines efficiently
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    total_lines += sum(1 for _ in f)
                
                # Get size
                total_size += os.path.getsize(path)
            except Exception as e:
                LOGGER.error(f"Error reading {path}: {e}")
                continue
        
        # Format total size
        if total_size < 1024:
            size_str = f"{total_size} B"
        elif total_size < 1024 * 1024:
            size_str = f"{total_size / 1024:.2f} KB"
        elif total_size < 1024 * 1024 * 1024:
            size_str = f"{total_size / (1024 * 1024):.2f} MB"
        else:
            size_str = f"{total_size / (1024 * 1024 * 1024):.2f} GB"
        
        # Get free disk space
        try:
            stat = shutil.disk_usage("/tmp/workspace/PmOfBangladesh/ulp/data")
            free_bytes = stat.free
            if free_bytes < 1024:
                free_str = f"{free_bytes} B"
            elif free_bytes < 1024 * 1024:
                free_str = f"{free_bytes / 1024:.2f} KB"
            elif free_bytes < 1024 * 1024 * 1024:
                free_str = f"{free_bytes / (1024 * 1024):.2f} MB"
            else:
                free_str = f"{free_bytes / (1024 * 1024 * 1024):.2f} GB"
        except Exception as e:
            LOGGER.error(f"Error getting disk space: {e}")
            free_str = "Unknown"
        
        return total_lines, size_str, free_str
    except Exception as e:
        LOGGER.error(f"Error getting DB stats: {e}")
        return 0, "0 B", "0 B"


async def _scan_and_count_domains() -> Tuple[Counter, int]:
    """
    Scan database files in streaming mode and count domains.
    Returns only top 20 domains to save memory.
    """
    domain_counter = Counter()
    total_lines = 0
    
    try:
        paths = list(Path("/tmp/workspace/PmOfBangladesh/ulp/data").glob("*.txt"))
        
        for path in paths:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        total_lines += 1
                        
                        # Extract domain from the line
                        # Format: domain:user:pass or url|user|pass etc
                        parts = re.split(r'[:|\;]', line, maxsplit=2)
                        if len(parts) >= 2:
                            identifier = parts[0].strip()
                            if identifier:
                                domain = _extract_domain(identifier)
                                domain_counter[domain] += 1
            except Exception as e:
                LOGGER.error(f"Error reading {path}: {e}")
                continue
        
        return domain_counter, total_lines
    except Exception as e:
        LOGGER.error(f"Error scanning database: {e}")
        return Counter(), 0


def _build_top_20_domains_output(domain_counter: Counter, total_lines: int, db_size: str, free_size: str) -> str:
    """Build the top 20 domains output message."""
    lines = ["🌐 **Top 20 Domains**\n"]
    
    # Get top 20
    top_20 = domain_counter.most_common(20)
    
    for i, (domain, count) in enumerate(top_20, 1):
        lines.append(f"{i}. `{domain}` — {count:,}")
    
    lines.extend([
        "",
        f"**Total Valid Line**: {total_lines:,}",
        f"**Total DB size**: {db_size}",
        f"**Free size**: {free_size}",
    ])
    
    return "\n".join(lines)



@ItsMrULPBot.on(events.NewMessage(pattern=_summary_pattern))
@new_task
async def summary_handler(event, bot):
    """Handle /summary command - scan DB and show top 20 domains with statistics."""
    sender = await event.get_sender()
    from helpers import add_user
    add_user(sender.id)
    
    chat_id = event.chat_id
    user_id = sender.id

    msg = await send_message(chat_id, "**🔍 Scanning Database For Domains... Please Wait ⏳**")
    if not msg:
        return

    try:
        # Measure scan time
        t0 = time.perf_counter()
        
        # Scan database in streaming mode
        domain_counter, total_lines = await _scan_and_count_domains()
        
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        
        # Get database statistics
        db_lines, db_size, free_size = _get_db_stats()
    except Exception as exc:
        LOGGER.error(f"summary_handler error: {exc}")
        await edit_message(chat_id, msg.id, "**❌ Error Scanning Database**")
        return

    if not domain_counter:
        await edit_message(chat_id, msg.id, "**❌ No Domains Found In Database**")
        return

    # Log the summary scan
    log_user_extraction(user_id, None, "SUMMARY", len(domain_counter), source="database_scan")

    # Build and display top 20 domains
    output = _build_top_20_domains_output(domain_counter, total_lines, db_size, free_size)
    await edit_message(chat_id, msg.id, output, parse_mode="markdown")

