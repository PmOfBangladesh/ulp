import asyncio
import os
import re
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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
from utils.engine import RG_BINARY, THREAD_POOL, collect_datastore_paths

# ── regex / command pattern ──
prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)

_OWNER_ONLY = {config.OWNER_ID}


def _owner_auth(uid: int) -> bool:
    return uid in _OWNER_ONLY
_summary_pattern = re.compile(rf"^[{prefixes}]summary$", re.IGNORECASE)
CREDENTIAL_SEPARATORS = r'[:;|]'
_DOMAIN_URL_RE = re.compile(r'(?:https?|ftp)://(?:www\.)?([^/:?#]+)')

# ── cache ──
_summary_cache: Optional[Dict] = None
_cache_lock = asyncio.Lock()
_cache_ready = asyncio.Event()
_CACHE_TTL = 600          # 10 minutes
_CACHE_BUILD_TIMEOUT = 900  # 15 minutes max for first build
_SCAN_TIMEOUT = 600         # 10 minutes max per scan
_last_cache_time: float = 0


# ═══════════════════════════════════════════════════════════════════
# domain extraction helpers
# ═══════════════════════════════════════════════════════════════════

def _extract_domain(identifier: str) -> str:
    """Extract domain from URL, email, or raw identifier."""
    if identifier.startswith(('http://', 'https://', 'ftp://')):
        match = _DOMAIN_URL_RE.match(identifier)
        if match:
            return match.group(1)
    elif '@' in identifier:
        return identifier.split('@')[1]
    return identifier


def _resolve_db_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def _extract_identifier(line: str) -> str:
    """Extract the first field (URL/email/domain) from a DB line."""
    if line.startswith(('http://', 'https://', 'ftp://')):
        protocol_end = line.find('://')
        if protocol_end < 0:
            return line.strip()
        protocol_end += 3
        colon_idx = line.find(':', protocol_end)
        pipe_idx = line.find('|', protocol_end)
        if colon_idx >= 0 and pipe_idx >= 0:
            cred_sep_idx = min(colon_idx, pipe_idx)
        elif colon_idx >= 0:
            cred_sep_idx = colon_idx
        elif pipe_idx >= 0:
            cred_sep_idx = pipe_idx
        else:
            cred_sep_idx = -1
        url_part = line[:cred_sep_idx] if cred_sep_idx > protocol_end else line
        try:
            parsed = urlparse(url_part)
            if parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass
        return url_part
    parts = re.split(CREDENTIAL_SEPARATORS, line, maxsplit=1)
    return parts[0].strip() if parts else line.strip()


# ═══════════════════════════════════════════════════════════════════
# scanning engine — ripgrep for max speed
# ═══════════════════════════════════════════════════════════════════

async def _scan_with_ripgrep(
    progress_callback=None
) -> Tuple[Counter, int, float]:
    """
    Scan all .txt files with ripgrep for maximum speed.
    
    Args:
        progress_callback: Optional async callable(total_lines, elapsed_s)
    
    Returns:
        (domain_counter, total_lines, elapsed_seconds)
    """
    domain_counter: Counter = Counter()
    total_lines = 0
    t_start = time.perf_counter()

    db_dir = _resolve_db_dir()
    file_paths = [str(p) for p in sorted(db_dir.glob("*.txt"))]
    if not file_paths:
        return Counter(), 0, 0

    file_count = len(file_paths)
    LOGGER.info(f"Summary scan: {file_count} files, using ripgrep")

    proc = await asyncio.create_subprocess_exec(
        RG_BINARY,
        '-o', '--no-filename', '--no-heading',
        '-N', '--pcre2',
        r'^(?:(?:https?|ftp)://[^\s:;|]+|[^\s:;|]+)',
        *file_paths,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    buffer = b''
    last_progress = t_start

    while True:
        chunk = await proc.stdout.read(524288)  # 512 KB chunks
        if not chunk:
            break
        buffer += chunk
        while b'\n' in buffer:
            line_bytes, buffer = buffer.split(b'\n', 1)
            total_lines += 1
            identifier = line_bytes.decode(errors='replace').rstrip('\r')
            if not identifier:
                continue
            domain = _extract_domain(identifier)
            if domain:
                domain_counter[domain] += 1

        # Progress update every 2 seconds
        now = time.perf_counter()
        if progress_callback and (now - last_progress) >= 2.0:
            last_progress = now
            elapsed = now - t_start
            try:
                await progress_callback(total_lines, elapsed)
            except Exception:
                pass

    # Handle trailing data
    if buffer:
        total_lines += 1
        identifier = buffer.decode(errors='replace').rstrip('\r')
        if identifier:
            domain = _extract_domain(identifier)
            if domain:
                domain_counter[domain] += 1

    # Ensure subprocess exits
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    elapsed = time.perf_counter() - t_start
    return domain_counter, total_lines, elapsed


async def _scan_with_python(
    progress_callback=None
) -> Tuple[Counter, int, float]:
    """Fallback: pure Python scanner using thread pool for max CPU."""
    domain_counter: Counter = Counter()
    total_lines = 0
    t_start = time.perf_counter()

    db_dir = _resolve_db_dir()
    file_paths = sorted(db_dir.glob("*.txt"))
    if not file_paths:
        return Counter(), 0, 0

    def _scan_all() -> Tuple[Counter, int]:
        dc: Counter = Counter()
        tl = 0
        for path in file_paths:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        tl += 1
                        ident = _extract_identifier(line)
                        if not ident:
                            continue
                        domain = _extract_domain(ident)
                        if domain:
                            dc[domain] += 1
            except Exception as e:
                LOGGER.error(f"Error scanning {path}: {e}")
        return dc, tl

    loop = asyncio.get_running_loop()
    dc, tl = await loop.run_in_executor(THREAD_POOL, _scan_all)

    elapsed = time.perf_counter() - t_start
    return dc, tl, elapsed


async def _run_scan(
    progress_callback=None
) -> Tuple[Counter, int, float]:
    """Run the best available scanner."""
    try:
        return await asyncio.wait_for(
            _scan_with_ripgrep(progress_callback),
            timeout=_SCAN_TIMEOUT
        )
    except FileNotFoundError:
        LOGGER.warning("ripgrep not found → Python fallback")
    except asyncio.TimeoutError:
        LOGGER.warning("ripgrep timed out → Python fallback")
    except Exception as e:
        LOGGER.warning(f"ripgrep failed ({e}) → Python fallback")

    try:
        return await asyncio.wait_for(
            _scan_with_python(progress_callback),
            timeout=_SCAN_TIMEOUT
        )
    except asyncio.TimeoutError:
        LOGGER.error("Python scanner timed out")
    except Exception as e:
        LOGGER.error(f"Python scanner failed: {e}")

    return Counter(), 0, 0


# ═══════════════════════════════════════════════════════════════════
# stats helpers
# ═══════════════════════════════════════════════════════════════════

def _get_db_stats() -> Tuple[str, str, str]:
    """Get total DB size, free disk space, and last-updated date."""
    try:
        db_dir = _resolve_db_dir()
        paths = list(db_dir.glob("*.txt"))
        if not paths:
            return "0 B", "0 B", "Unknown"

        total_size = sum(os.path.getsize(p) for p in paths)

        # Format size
        if total_size < 1024:
            size_str = f"{total_size} B"
        elif total_size < 1024 * 1024:
            size_str = f"{total_size / 1024:.2f} KB"
        elif total_size < 1024 * 1024 * 1024:
            size_str = f"{total_size / (1024 * 1024):.2f} MB"
        else:
            size_str = f"{total_size / (1024 * 1024 * 1024):.2f} GB"

        # Free disk
        try:
            stat = shutil.disk_usage(str(db_dir))
            free_bytes = stat.free
            if free_bytes < 1024:
                free_str = f"{free_bytes} B"
            elif free_bytes < 1024 * 1024:
                free_str = f"{free_bytes / 1024:.2f} KB"
            elif free_bytes < 1024 * 1024 * 1024:
                free_str = f"{free_bytes / (1024 * 1024):.2f} MB"
            else:
                free_str = f"{free_bytes / (1024 * 1024 * 1024):.2f} GB"
        except Exception:
            free_str = "Unknown"

        # Last updated
        last_updated = "Unknown"
        try:
            latest = max(os.path.getmtime(str(p)) for p in paths)
            last_updated = datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        return size_str, free_str, last_updated
    except Exception:
        return "0 B", "0 B", "Unknown"


def _build_output(
    domain_counter: Counter,
    total_lines: int,
    db_size: str,
    free_size: str,
    last_updated: str,
    elapsed_s: float,
) -> str:
    """Build the summary output message."""
    total_unique = len(domain_counter)

    lines = [
        "📊 **Database Summary**",
        "",
        f"💾 **Total DB Size** : `{db_size}`",
        f"🗂️  **Free Disk**     : `{free_size}`",
        f"🕐 **Last Updated**  : `{last_updated}`",
        f"🔑 **Total Combos**  : `{total_lines:,}`",
        f"🌐 **Unique Domains**: `{total_unique:,}`",
        f"⚡ **Scan Time**     : `{elapsed_s:.1f}s`",
        "",
        "**━━━ Top 5 Domains ━━━**",
    ]

    top_5 = domain_counter.most_common(5)
    for i, (domain, count) in enumerate(top_5, 1):
        lines.append(f"  {i}. `{domain}` — {count:,}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# cache management
# ═══════════════════════════════════════════════════════════════════

async def _rebuild_cache() -> None:
    """Run full scan and store result. Called silently in background."""
    global _summary_cache, _last_cache_time

    LOGGER.info("Summary cache rebuild started (background)")
    t0 = time.perf_counter()

    try:
        dc, lines, elapsed = await _run_scan()
        size_str, free_str, updated = _get_db_stats()
        _summary_cache = {
            "domain_counter": dc,
            "total_lines": lines,
            "db_size": size_str,
            "free_size": free_str,
            "last_updated": updated,
            "elapsed_s": elapsed,
        }
        _last_cache_time = time.time()
        LOGGER.info(
            f"Summary cache rebuilt: {lines:,} lines, "
            f"{len(dc):,} domains in {elapsed:.1f}s"
        )
    except Exception:
        LOGGER.exception("Summary cache rebuild failed")
        _summary_cache = {
            "domain_counter": Counter(),
            "total_lines": 0,
            "db_size": "Unknown",
            "free_size": "Unknown",
            "last_updated": "Unknown",
            "elapsed_s": 0,
        }
    finally:
        _cache_ready.set()


def _is_cache_stale() -> bool:
    """Check if cache is older than TTL."""
    return (time.time() - _last_cache_time) > _CACHE_TTL


async def _cache_updater() -> None:
    """Background task: rebuild cache every TTL seconds."""
    await _rebuild_cache()
    while True:
        await asyncio.sleep(_CACHE_TTL)
        await _rebuild_cache()


async def trigger_cache_refresh() -> None:
    """Force cache rebuild (called by /dl and /add after new data)."""
    global _summary_cache, _last_cache_time
    _cache_ready.clear()
    _summary_cache = None
    _last_cache_time = 0
    asyncio.create_task(_rebuild_cache())
    LOGGER.info("Summary cache refresh triggered — rebuilding in background")


# Start background cache updater
try:
    _updater_task = asyncio.create_task(_cache_updater())
except RuntimeError:
    _updater_task = None  # no event loop (tests)


# ═══════════════════════════════════════════════════════════════════
# /summary command handler
# ═══════════════════════════════════════════════════════════════════

@ItsMrULPBot.on(events.NewMessage(pattern=_summary_pattern))
@new_task
async def summary_handler(event, bot):
    """
    /summary — Show database statistics with Top 5 domains.
    
    Cache is built at startup and refreshed every 10 minutes.
    If cache is not ready, waits for background build to finish (max 30s polling).
    """
    global _summary_cache, _last_cache_time

    sender = await event.get_sender()
    if not _owner_auth(sender.id):
        LOGGER.warning(f"Unauthorized /summary attempt by {sender.id}")
        return
    from helpers import add_user
    add_user(sender.id)

    chat_id = event.chat_id
    user_id = sender.id

    # ── Wait for cache if not ready (poll up to 30s) ──
    if not _cache_ready.is_set():
        msg = await send_message(
            chat_id, "**📊 Building cache... Please wait ⏳**"
        )
        if not msg:
            return

        waited = 0
        while not _cache_ready.is_set() and waited < 30:
            await asyncio.sleep(1)
            waited += 1
            if waited % 3 == 0:
                try:
                    await edit_message(
                        chat_id, msg.id,
                        f"**📊 Building cache... Please wait ⏳**\n"
                        f"_Still scanning ({waited}s)..._"
                    )
                except Exception:
                    pass

        if not _cache_ready.is_set():
            await edit_message(
                chat_id, msg.id,
                "**⏳ Cache still building — try /summary again in 1-2 min**"
            )
            return

        await edit_message(chat_id, msg.id, "**📊 Cache ready — fetching...**")

    # ── Serve from cache ──
    cache = _summary_cache
    if cache is None:
        await send_message(chat_id, "**❌ No data — try again in 1 min**")
        return

    dc = cache["domain_counter"]
    total_lines = cache["total_lines"]

    if not dc:
        await send_message(
            chat_id,
            "**❌ No Domains Found In Database**\n"
            "Try again later (cache refreshes every 10 minutes)."
        )
        return

    # Trigger silent background refresh if stale
    if _is_cache_stale():
        asyncio.create_task(_rebuild_cache())

    msg = await send_message(chat_id, "**📊 Fetching summary…**")
    if not msg:
        return

    log_user_extraction(user_id, None, "SUMMARY", total_lines, source="database_scan")
    output = _build_output(
        dc, total_lines,
        cache["db_size"], cache["free_size"],
        cache["last_updated"], cache.get("elapsed_s", 0),
    )
    await edit_message(chat_id, msg.id, output, parse_mode="markdown")
