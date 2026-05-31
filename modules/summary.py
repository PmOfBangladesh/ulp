import asyncio
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

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
        # Extract domain from URL (supports http, https, and ftp)
        match = re.match(r'(?:https?|ftp)://(?:www\.)?([^/:?#]+)', identifier)
        if match:
            return match.group(1)
    elif '@' in identifier:
        # Extract domain from email
        return identifier.split('@')[1]
    # For usernames or phone numbers, just return the identifier
    return identifier


def _get_db_stats(total_lines: int = None) -> Tuple[int, str, str]:
    """Get total lines, total size, and free disk space.
    If total_lines is provided, it will be used instead of recounting."""
    try:
        paths = list(Path("/tmp/workspace/PmOfBangladesh/ulp/data").glob("*.txt"))
        
        if not paths:
            return total_lines or 0, "0 B", "0 B"
        
        total_size = 0
        counted_lines = 0
        
        for path in paths:
            try:
                # Count lines only if not provided
                if total_lines is None:
                    with open(path, 'r', encoding='utf-8', errors='replace') as f:
                        counted_lines += sum(1 for _ in f)
                
                # Get size
                total_size += os.path.getsize(path)
            except Exception as e:
                LOGGER.error(f"Error reading {path}: {e}")
                continue
        
        # Use provided total_lines if available, otherwise use counted lines
        final_lines = total_lines if total_lines is not None else counted_lines
        
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
        
        return final_lines, size_str, free_str
    except Exception as e:
        LOGGER.error(f"Error getting DB stats: {e}")
        return total_lines or 0, "0 B", "0 B"


def _extract_identifier(line: str) -> str:
    """Extract identifier (URL, email, or domain) from a database line.
    
    Handles formats:
    - domain:user:pass (plain domain)
    - url|user|pass (URL with pipe separator)
    - email:user:pass (email format)
    - url:user:pass (URL with colon separator)
    """
    # Check if line starts with a URL protocol
    if line.startswith(('http://', 'https://', 'ftp://')):
        # Find the position after the protocol (after ://)
        protocol_end = line.find('://') + 3
        
        # Find the first credential separator (: or |) after the protocol
        cred_sep_idx = -1
        for i in range(protocol_end, len(line)):
            if line[i] in ':|':
                cred_sep_idx = i
                break
        
        if cred_sep_idx > protocol_end:
            url_part = line[:cred_sep_idx]
        else:
            url_part = line
        
        # Parse the URL to extract the network location (netloc includes domain:port)
        try:
            parsed = urlparse(url_part)
            if parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except ValueError:
            # urlparse raises ValueError for invalid URLs
            pass
        
        # Fallback: return the URL part as-is if parsing fails
        return url_part
    
    # Not a URL, use standard split for email or domain
    # Split on first occurrence of : or | to separate identifier from credentials
    parts = re.split(r'[:;|]', line, maxsplit=1)
    return parts[0].strip() if parts else line.strip()


async def _scan_and_count_domains() -> Tuple[Counter, int]:
    """
    Scan database files in streaming mode and count domains.
    Returns domain counter and total lines. Uses threading to avoid blocking event loop.
    """
    def _scan_domains():
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
                            
                            # Extract identifier from the line (handles URLs, emails, domains)
                            identifier = _extract_identifier(line)
                            if not identifier:
                                continue
                            
                            # Extract domain from identifier
                            domain = _extract_domain(identifier)
                            if domain:
                                domain_counter[domain] += 1
                except Exception as e:
                    LOGGER.error(f"Error reading {path}: {e}")
                    continue
            
            return domain_counter, total_lines
        except Exception as e:
            LOGGER.error(f"Error scanning database: {e}")
            return Counter(), 0
    
    # Run blocking I/O in a thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _scan_domains)


def _build_top_20_domains_output(domain_counter: Counter, total_lines: int, db_size: str, free_size: str) -> str:
    """Build the top 20 domains output message."""
    lines = ["🌐 **Top 20 Domains**\n"]
    
    # Get top 20
    top_20 = domain_counter.most_common(20)
    
    for i, (domain, count) in enumerate(top_20, 1):
        lines.append(f"{i}. `{domain}` — {count:,}")
    
    lines.extend([
        "",
        f"**Total Valid Lines**: {total_lines:,}",
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
        
        # Get database statistics (passing total_lines to avoid recounting)
        db_lines, db_size, free_size = _get_db_stats(total_lines)
    except Exception as exc:
        LOGGER.error(f"summary_handler error: {exc}")
        await edit_message(chat_id, msg.id, "**❌ Error Scanning Database**")
        return

    if not domain_counter:
        await edit_message(chat_id, msg.id, "**❌ No Domains Found In Database**")
        return

    # Log the summary scan (using total_lines to reflect actual database entries scanned)
    log_user_extraction(user_id, None, "SUMMARY", total_lines, source="database_scan")

    # Build and display top 20 domains
    output = _build_top_20_domains_output(domain_counter, total_lines, db_size, free_size)
    await edit_message(chat_id, msg.id, output, parse_mode="markdown")

