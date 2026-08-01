"""
/dl command — download media from Telegram channels (private / public).

Examples
--------
/dl https://t.me/c/123456789/100  2h        ← last 2 hours
/dl https://t.me/c/123456789      1d        ← last 1 day
/dl https://t.me/c/123456789      774-886   ← message ID range
/dl https://t.me/+AbCdEfGh        30m       ← invite link
/dl @channelname                  1h        ← public username

Requirements
------------
* User must log in **once** with a phone number (interactive OTP via chat).
* After the first login the session is persisted → no re-login needed.
* Uses **StringSession** (plain‑text file) → no SQLite, no "database is locked".
"""

import asyncio
import os
import re
import time
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest

import config
from bot import ItsMrULPBot
from helpers import LOGGER, edit_message, new_task, send_message
from helpers.botutils import get_args_str

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
USER_SESSION_FILE = "dl_session.txt"   # plain‑text StringSession → no SQLite locks
SAVE_DIR = "data"
os.makedirs(SAVE_DIR, exist_ok=True)

# update the status message every N files (range mode) or every N seconds
UPDATE_EVERY_FILES = 5
UPDATE_EVERY_SECS = 3.0

# parallel download concurrency
MAX_CONCURRENT_DOWNLOADS = 5

# command pattern  (e.g. /dl, !dl, .dl, …)
_prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)
DL_PATTERN = re.compile(rf"^[{_prefixes}]dl(?:\s+.+)?$", re.IGNORECASE)

# command-like messages  (we skip these inside the login flow)
CMD_LIKE = re.compile(rf"^[{_prefixes}]\w")

# ---------------------------------------------------------------------------
# global state
# ---------------------------------------------------------------------------
_user_client: TelegramClient | None = None
_user_client_lock = asyncio.Lock()
_download_lock = asyncio.Lock()
_download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# per-chat login state machine
# keys  : chat_id
# value : dict with the fields used by the login flow + saved download params
_login_state: dict = {}

# cached logged‑in user info  (first name, user_id)
_logged_in_user: dict | None = None


# ======================= helpers =======================

def _load_session_string() -> str:
    """Read the saved StringSession from the text file."""
    if os.path.exists(USER_SESSION_FILE):
        try:
            with open(USER_SESSION_FILE, "r") as f:
                data = f.read().strip()
                if data:
                    return data
        except Exception:
            pass
    return ""


def _save_session_string(session_str: str) -> None:
    """Persist the StringSession to a plain‑text file."""
    with open(USER_SESSION_FILE, "w") as f:
        f.write(session_str)


def _parse_duration(raw: str) -> int | None:
    """Return seconds from strings like ``2h``, ``30m``, ``1d``."""
    m = re.match(r"^(\d+)([hmd])$", raw.strip().lower())
    if not m:
        return None
    return int(m.group(1)) * {"h": 3600, "m": 60, "d": 86400}[m.group(2)]


def _parse_channel(link: str):
    """Convert a t.me link into a chat identifier that Telethon understands.

    Returns
    -------
    int   – private supergroup / channel ID  (``-100xxx``)
    str   – invite hash or public @username
    None  – couldn't parse
    """
    link = link.strip().rstrip("/")
    # private channel  →  https://t.me/c/123456789/100
    if "/c/" in link:
        parts = link.split("/")
        for i, p in enumerate(parts):
            if p == "c" and i + 1 < len(parts):
                return int("-100" + parts[i + 1])
        return None
    # invite hash  →  https://t.me/+AbCdEfGh
    if "/+" in link:
        return link
    # public  →  @username  or  https://t.me/username
    username = link.split("/")[-1].lstrip("@")
    return username if username else None


async def _migrate_old_session() -> bool:
    """One‑time migration: convert old SQLite session → StringSession text file."""
    OLD_SESSION = "dl_session.session"       # SQLite file from previous version
    OLD_SESSION_NAME = "dl_session"          # Telethon strips .session when given a string
    if not os.path.exists(OLD_SESSION) or os.path.exists(USER_SESSION_FILE):
        return False  # nothing to migrate (or already done)

    LOGGER.info("Attempting migration from old SQLite session → StringSession …")
    try:
        client = TelegramClient(OLD_SESSION_NAME, config.API_ID, config.API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        # extract session string and save
        session_str = client.session.save()
        _save_session_string(session_str)
        await client.disconnect()
        # remove old SQLite file (plus WAL/SHM if present)
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(OLD_SESSION + suffix)
            except OSError:
                pass
        LOGGER.info("Session migration successful – old SQLite file removed")
        return True
    except Exception as e:
        LOGGER.warning(f"Session migration failed: {e}")
        return False


async def _get_logged_in_name(client: TelegramClient) -> str:
    """Return the first name of the logged‑in user (cached)."""
    global _logged_in_user
    if _logged_in_user is not None:
        return _logged_in_user.get("first_name", "User")
    try:
        me = await client.get_me()
        _logged_in_user = {"first_name": me.first_name or "User", "user_id": me.id}
        return _logged_in_user["first_name"]
    except Exception:
        return "User"


async def _download_single_msg(
    client: TelegramClient,
    msg,
    files_counter: list,
    bytes_counter: list,
    t_start: float,
    last_update: list,
    status_msg,
):
    """Download media from a single already-fetched message (runs inside semaphore)."""
    async with _download_semaphore:
        try:
            fp = await client.download_media(msg, file=SAVE_DIR)
        except FloodWaitError as fw:
            LOGGER.warning(f"FloodWait {fw.seconds}s – waiting…")
            await asyncio.sleep(fw.seconds + 1)
            try:
                fp = await client.download_media(msg, file=SAVE_DIR)
            except Exception:
                return
        except Exception as exc:
            LOGGER.debug(f"skip msg {getattr(msg, 'id', '?')}: {exc}")
            return

        if fp:
            files_counter[0] += 1
            try:
                bytes_counter[0] += os.path.getsize(fp)
            except OSError:
                pass

        # periodic status update
        now = time.time()
        if files_counter[0] % UPDATE_EVERY_FILES == 0 or (now - last_update[0]) >= UPDATE_EVERY_SECS:
            last_update[0] = now
            elapsed = now - t_start
            try:
                await status_msg.edit(
                    f"📥 **Downloading...**\n"
                    f"Files: **{files_counter[0]}**\n"
                    f"Elapsed: **{elapsed:.0f}s**"
                )
            except Exception:
                pass


async def _download_range(
    client: TelegramClient,
    chat_entity,
    start_id: int,
    end_id: int,
    status_msg,
) -> tuple[int, int]:
    """Download media from a message-ID range — batch fetching for efficiency."""
    files_counter = [0]
    bytes_counter = [0]
    t_start = time.time()
    last_update = [t_start]

    total_msgs = end_id - start_id + 1
    all_ids = list(range(start_id, end_id + 1))

    FETCH_BATCH = 100   # Telegram get_messages limit per call
    scanned = 0

    for batch_start in range(0, len(all_ids), FETCH_BATCH):
        batch_ids = all_ids[batch_start : batch_start + FETCH_BATCH]

        # update status during scanning phase
        try:
            await status_msg.edit(
                    f"🔍 **Scanning...**  {scanned}/{total_msgs} msgs checked\n"
                    f"Files found: **{files_counter[0]}**"
            )
        except Exception:
            pass

        # batch-fetch messages (1 API call per 100 IDs instead of 100 calls)
        try:
            msgs = await client.get_messages(chat_entity, ids=batch_ids)
        except FloodWaitError as fw:
            LOGGER.warning(f"FloodWait {fw.seconds}s – waiting…")
            await asyncio.sleep(fw.seconds + 1)
            try:
                msgs = await client.get_messages(chat_entity, ids=batch_ids)
            except Exception:
                scanned += len(batch_ids)
                continue
        except Exception:
            scanned += len(batch_ids)
            continue

        # filter valid messages with media, updating scanned count
        valid_msgs = []
        for msg in msgs:
            scanned += 1
            if msg is not None and msg.media:
                valid_msgs.append(msg)

        if not valid_msgs:
            continue

        # download valid messages in parallel (limited by semaphore)
        tasks = []
        for msg in valid_msgs:
            task = asyncio.create_task(
                _download_single_msg(
                    client, msg,
                    files_counter=files_counter, bytes_counter=bytes_counter,
                    t_start=t_start, last_update=last_update, status_msg=status_msg,
                )
            )
            tasks.append(task)

        await asyncio.gather(*tasks)

    return files_counter[0], bytes_counter[0]


async def _download_time(
    client: TelegramClient,
    chat_entity,
    duration_secs: int,
    param_label: str,
    status_msg,
) -> tuple[int, int]:
    """Download media newer than *duration_secs* with parallel downloads."""
    files_counter = [0]
    bytes_counter = [0]
    t_start = time.time()
    last_update = [t_start]

    # Use offset_date so Telegram only returns messages within the time window
    offset_date = datetime.now(timezone.utc) - timedelta(seconds=duration_secs)

    await status_msg.edit("🔍 Scanning Messages\n_This may take a moment..._")

    # collect messages first (fast iteration), then download in parallel batches
    msgs_to_download: list = []
    scan_count = 0
    try:
        async for msg in client.iter_messages(chat_entity, offset_date=offset_date):
            scan_count += 1
            if msg.media:
                msgs_to_download.append(msg)
            # occasional scanning progress update
            if scan_count % 500 == 0:
                try:
                    await status_msg.edit(
                        f"🔍 **Scanning...**  {scan_count} msgs checked\n"
                        f"Media found: **{len(msgs_to_download)}**"
                    )
                except Exception:
                    pass
    except FloodWaitError as fw:
        LOGGER.warning(f"FloodWait during iteration: {fw.seconds}s")
        await asyncio.sleep(fw.seconds + 1)

    total_msgs = len(msgs_to_download)
    if total_msgs == 0:
        return 0, 0

    await status_msg.edit(f"📥 Downloading\nDownloading **{total_msgs}** media files...")

    # download in parallel with semaphore
    tasks = []
    for idx, msg in enumerate(msgs_to_download):
        task = asyncio.create_task(
            _time_download_worker(
                client, msg, idx, total_msgs, param_label, status_msg,
                files_counter, bytes_counter, t_start, last_update,
            )
        )
        tasks.append(task)

    await asyncio.gather(*tasks)

    return files_counter[0], bytes_counter[0]


async def _time_download_worker(
    client: TelegramClient,
    msg,
    idx: int,
    total: int,
    param_label: str,
    status_msg,
    files_counter: list,
    bytes_counter: list,
    t_start: float,
    last_update: list,
):
    """Download a single message (for time‑based mode)."""
    async with _download_semaphore:
        try:
            fp = await client.download_media(msg, file=SAVE_DIR)
        except FloodWaitError as fw:
            LOGGER.warning(f"FloodWait {fw.seconds}s – waiting…")
            await asyncio.sleep(fw.seconds + 1)
            try:
                fp = await client.download_media(msg, file=SAVE_DIR)
            except Exception:
                return
        except Exception:
            return

        if fp:
            files_counter[0] += 1
            try:
                bytes_counter[0] += os.path.getsize(fp)
            except OSError:
                pass

        now = time.time()
        # update every N files OR every N seconds (whichever comes first)
        if files_counter[0] % UPDATE_EVERY_FILES == 0 or (now - last_update[0]) >= UPDATE_EVERY_SECS:
            last_update[0] = now
            elapsed = now - t_start
            try:
                await status_msg.edit(
                    f"📥 **Downloading...**\n"
                    f"Files: **{files_counter[0]}**\n"
                    f"Elapsed: **{elapsed:.0f}s**"
                )
            except Exception:
                pass


# ==================== user-client management ====================

async def _get_user_client() -> TelegramClient | None:
    """Return a **connected & authorised** user client, or ``None``.

    Uses ``StringSession`` stored in a plain‑text file → **zero SQLite**.
    """
    global _user_client

    async with _user_client_lock:
        # reuse cached client when possible
        if _user_client is not None and _user_client.is_connected():
            try:
                if await _user_client.is_user_authorized():
                    return _user_client
            except Exception:
                pass  # fall through → recreate

        # close any stale cached instance
        if _user_client is not None:
            try:
                await _user_client.disconnect()
            except Exception:
                pass
            _user_client = None

        # one‑time migration from old SQLite session (if present)
        await _migrate_old_session()

        # load session string from disk
        session_str = _load_session_string()

        if not session_str:
            return None   # no saved session → login required

        # create & connect using StringSession
        client = TelegramClient(
            StringSession(session_str), config.API_ID, config.API_HASH,
            connection_retries=3,
        )
        try:
            await client.connect()
        except Exception as exc:
            LOGGER.error(f"user client connect error: {exc}")
            return None

        if await client.is_user_authorized():
            _user_client = client
            # cache user info for display
            global _logged_in_user
            _logged_in_user = None  # force refresh
            # fire‑and‑forget: resolve the name in background
            asyncio.create_task(_resolve_and_show_name(client))
            LOGGER.info("user client ready (existing session)")
            return client

        # session string exists but is invalid / expired
        await client.disconnect()
        # delete the stale session file so a fresh login can be started
        try:
            os.remove(USER_SESSION_FILE)
        except OSError:
            pass
        return None


async def _resolve_and_show_name(client: TelegramClient) -> None:
    """Resolve the logged‑in user's name and store it for display."""
    try:
        name = await _get_logged_in_name(client)
        LOGGER.info(f"Logged in as: {name}")
    except Exception:
        pass


async def _disconnect_user_client():
    """Disconnect (but keep session file so next call reconnects)."""
    global _user_client
    async with _user_client_lock:
        if _user_client is not None:
            try:
                await _user_client.disconnect()
            except Exception:
                pass
            _user_client = None


# ======================= download helpers ======================

async def _resolve_invite(client: TelegramClient, chat_entity: str) -> int:
    """Join an invite link and return the resolved chat ID."""
    hash_part = chat_entity.rstrip("/").split("+")[-1]
    updates = await client(ImportChatInviteRequest(hash_part))
    if updates.chats:
        return updates.chats[0].id
    raise ValueError("Could not resolve invite link")


async def _run_download(
    chat_id: int,
    client: TelegramClient,
    chat_entity,
    is_range: bool,
    start_id: int | None,
    end_id: int | None,
    duration_secs: int | None,
    param_label: str,
) -> None:
    """Core download routine — runs inside ``_download_lock``."""
    status = await send_message(chat_id, "⏳ Preparing Download\nInitializing download session...")
    if status is None:
        return

    total_files = total_bytes = 0
    t_start = time.time()

    try:
        # resolve invite links
        if isinstance(chat_entity, str) and chat_entity.startswith("https://t.me/+"):
            await status.edit("🔗 Joining Channel\nResolving invite link...")
            try:
                chat_entity = await _resolve_invite(client, chat_entity)
            except Exception as exc:
                await status.edit(f"❌ Invite Failed\n`{exc}`")
                return

        if is_range:
            await status.edit(f"🔍 Scanning Range\nRange: `{start_id}` → `{end_id}`")
            total_files, total_bytes = await _download_range(
                client, chat_entity, start_id, end_id, status
            )
        else:
            total_files, total_bytes = await _download_time(
                client, chat_entity, duration_secs, param_label, status
            )

        # --- final summary ---
        elapsed = time.time() - t_start
        total_mb = total_bytes / (1024 * 1024)

        if total_files > 0:
            try:
                await status.edit(
                    f"✅ **Download Complete!**\n\n"
                    f"📁 **Files:** `{total_files}`\n"
                    f"💾 **Size:** `{total_mb:.2f} MB`\n"
                    f"⏱️ **Time:** `{elapsed:.1f}s`\n"
                    f"📂 **Saved to:** `{SAVE_DIR}/`"
                )
            except Exception:
                pass
        else:
            try:
                await status.edit("⚠️ No Media Found\nNo media files found in the specified range or duration.")
            except Exception:
                pass

    except FloodWaitError as fw:
        LOGGER.warning(f"FloodWait during download: {fw.seconds}s")
        try:
            await status.edit(f"⏳ Rate Limited\nTelegram asks to wait **{fw.seconds}s**.\nTry again later.")
        except Exception:
            pass
    except Exception as exc:
        LOGGER.error(f"download error: {exc}")
        try:
            await status.edit(f"❌ Download Failed\n`{exc}`")
        except Exception:
            pass

    # Trigger summary cache refresh so /summary reflects newly downloaded data
    try:
        from modules.summary import trigger_cache_refresh
        await trigger_cache_refresh()
    except Exception:
        pass  # non-critical — background updater will refresh within 2h


# ======================= /dl command handler =======================

@ItsMrULPBot.on(events.NewMessage(pattern=DL_PATTERN))
@new_task
async def dl_handler(event, bot):  # noqa: ARG001  (bot unused but kept for consistency)
    """Handle ``/dl <link> <duration|range>``."""
    chat_id = event.chat_id
    raw_args = get_args_str(event).strip()

    # clear any stale login state for this user  (new /dl always wins)
    _login_state.pop(chat_id, None)

    # ----- parse arguments -----
    if not raw_args:
        await send_message(
            chat_id,
            "❌ **Usage:**\n"
            "`/dl <channel_link> <duration|range>`\n\n"
            "**Examples:**\n"
            "`/dl https://t.me/c/123456789 2h`\n"
            "`/dl https://t.me/c/123456789 1d`\n"
            "`/dl https://t.me/c/123456789 774-886`\n"
            "`/dl https://t.me/+AbCdEfGh 30m`\n"
            "`/dl @channelname 1h`",
        )
        return

    # last whitespace-separated token = duration / range; rest = link
    parts = raw_args.rsplit(maxsplit=1)
    if len(parts) != 2:
        await send_message(chat_id, "❌ Invalid format. Use: `/dl <link> <duration|range>`")
        return

    link_raw, param_raw = parts

    # parse channel
    chat_entity = _parse_channel(link_raw)
    if chat_entity is None:
        await send_message(
            chat_id,
            "❌ Invalid channel link.\n"
            "Supported formats:\n"
            "• `https://t.me/c/123456789` (private)\n"
            "• `https://t.me/+AbCdEfGh` (invite)\n"
            "• `https://t.me/username` (public)\n"
            "• `@username`",
        )
        return

    # parse duration / range
    is_range = "-" in param_raw
    if is_range:
        try:
            start_id, end_id = map(int, param_raw.split("-"))
            if start_id > end_id:
                start_id, end_id = end_id, start_id
        except ValueError:
            await send_message(chat_id, "❌ Invalid Range\nUse format: `774-886`")
            return
        duration_secs = None
    else:
        duration_secs = _parse_duration(param_raw)
        if duration_secs is None:
            await send_message(chat_id, "❌ Invalid Duration\nUse: `2h`, `30m`, `1d`")
            return
        start_id = end_id = None

    # ----- obtain user client -----
    client = await _get_user_client()

    if client is None:
        # No valid session → start interactive login, but save the download
        # parameters so we can resume after login succeeds.
        _login_state[chat_id] = {
            "state": "awaiting_phone",
            "chat_entity": chat_entity,
            "is_range": is_range,
            "start_id": start_id,
            "end_id": end_id,
            "duration_secs": duration_secs,
            "param_label": param_raw,
            # fields filled during login flow
            "client": None,
            "phone": None,
            "phone_code_hash": None,
        }
        await send_message(
            chat_id,
            "🔐 **First-time setup — Login required**\n\n"
            "First-time setup — please send your phone number with country code.\n\n"
            "**Example:** `+8801XXXXXXXXX`\n\n"
            "Type `/cancel` to abort.",
        )
        return

    # already logged in → show who's logged in and start download
    name = await _get_logged_in_name(client)
    await send_message(chat_id, f"👤 Connected\nLogged in as **{name}** — starting download...")


    async with _download_lock:
        await _run_download(
            chat_id, client, chat_entity,
            is_range, start_id, end_id, duration_secs, param_raw,
        )


# ======================= interactive login flow =======================

@ItsMrULPBot.on(events.NewMessage(func=lambda e: e.chat_id in _login_state))
@new_task
async def _login_flow_handler(event, bot):  # noqa: ARG001
    """Handle phone → OTP → 2FA steps inside the login state machine."""
    chat_id = event.chat_id
    text = (event.text or "").strip()
    state = _login_state.get(chat_id)
    if state is None:
        return

    # ---- allow cancel at any step ----
    cmd_lower = text.lstrip("/!.,$#").lower()
    if cmd_lower in ("cancel", "stop"):
        _login_state.pop(chat_id, None)
        # clean up temporary client if any
        tmp = state.get("client")
        if tmp:
            try:
                await tmp.disconnect()
            except Exception:
                pass
        await send_message(chat_id, "❌ Login Cancelled\nSend `/dl` to try again.")
        return

    # ignore other commands while in login flow (let their own handlers run)
    if CMD_LIKE.match(text):
        return

    current_state = state["state"]

    # ================================================================
    #  STATE 1 — waiting for phone number
    # ================================================================
    if current_state == "awaiting_phone":
        phone = text.strip().lstrip("+")
        if not phone.isdigit() or len(phone) < 6:
            await send_message(chat_id, "❌ Invalid Phone\nPlease enter a valid phone number.\nType `/cancel` to abort.")
            return

        tmp = TelegramClient(
            StringSession(), config.API_ID, config.API_HASH
        )
        try:
            await tmp.connect()
        except Exception as exc:
            LOGGER.error(f"connect error: {exc}")
            await send_message(chat_id, f"❌ Connection Error\n`{exc}`")

            return

        try:
            sent = await tmp.send_code_request(phone)
        except PhoneNumberInvalidError:
            await tmp.disconnect()
            await send_message(chat_id, "❌ Invalid Phone\nThis phone number is not valid.\nType `/cancel` to abort.")
            return
        except FloodWaitError as fw:
            await tmp.disconnect()
            await send_message(chat_id, f"⏳ Flood Wait\nPlease wait **{fw.seconds}s** and try again.")
            return
        except Exception as exc:
            LOGGER.error(f"send_code_request error: {exc}")
            await tmp.disconnect()
            await send_message(chat_id, f"❌ Error\n`{exc}`\nType `/cancel` to abort.")
            return

        state["client"] = tmp
        state["phone"] = phone
        state["phone_code_hash"] = sent.phone_code_hash
        state["state"] = "awaiting_code"

        await send_message(chat_id, "📱 OTP Sent\nPlease enter the verification code you received.")

    # ================================================================
    #  STATE 2 — waiting for OTP code
    # ================================================================
    elif current_state == "awaiting_code":
        code = text.strip()
        tmp: TelegramClient = state.get("client")
        if tmp is None:
            _login_state.pop(chat_id, None)
            await send_message(chat_id, "❌ Session Expired\nStart again with `/dl`.")
            return

        try:
            await tmp.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["phone_code_hash"],
            )
        except SessionPasswordNeededError:
            state["state"] = "awaiting_2fa"
            await send_message(chat_id, "🔒 2FA Required\nPlease enter your 2FA password:")
            return
        except PhoneCodeInvalidError:
            await send_message(chat_id, "❌ Invalid Code\nPlease try again or type `/cancel`.")
            return
        except PhoneCodeExpiredError:
            await send_message(chat_id, "⏰ Code Expired\nStart again with `/dl` to get a new code.")
            try:
                await tmp.disconnect()
            except Exception:
                pass
            _login_state.pop(chat_id, None)
            return
        except FloodWaitError as fw:
            await send_message(chat_id, f"⏳ Flood Wait\nPlease wait **{fw.seconds}s** and try again.")
            return
        except Exception as exc:
            LOGGER.error(f"sign_in error: {exc}")
            await tmp.disconnect()
            _login_state.pop(chat_id, None)
            await send_message(chat_id, f"❌ Login Failed\n`{exc}`\nStart again with `/dl`.")
            return

        # --- success (no 2FA) ---
        await _finish_login(chat_id, tmp, state)

    # ================================================================
    #  STATE 3 — waiting for 2FA password
    # ================================================================
    elif current_state == "awaiting_2fa":
        password = text
        tmp: TelegramClient = state.get("client")
        if tmp is None:
            _login_state.pop(chat_id, None)
            await send_message(chat_id, "❌ Session Expired\nStart again with `/dl`.")
            return

        try:
            await tmp.sign_in(password=password)
        except PasswordHashInvalidError:
            await send_message(chat_id, "❌ Wrong Password\nPlease try again or type `/cancel`.")
            return
        except FloodWaitError as fw:
            await send_message(chat_id, f"⏳ Flood Wait\nPlease wait **{fw.seconds}s** and try again.")
            return
        except Exception as exc:
            LOGGER.error(f"2FA sign_in error: {exc}")
            await tmp.disconnect()
            _login_state.pop(chat_id, None)
            await send_message(chat_id, f"❌ Login Failed\n`{exc}`\nStart again with `/dl`.")
            return

        # --- success (2FA) ---
        await _finish_login(chat_id, tmp, state)


async def _finish_login(chat_id: int, client: TelegramClient, state: dict) -> None:
    """Store the authorised client globally, show who's logged in, and kick off download."""
    global _user_client, _logged_in_user

    # save session string to plain‑text file  (no SQLite!)
    session_str = client.session.save()
    _save_session_string(session_str)

    async with _user_client_lock:
        if _user_client is not None:
            try:
                await _user_client.disconnect()
            except Exception:
                pass
        _user_client = client

    # resolve the logged‑in user's name
    first_name = "User"
    try:
        me = await client.get_me()
        first_name = me.first_name or "User"
        _logged_in_user = {"first_name": first_name, "user_id": me.id}
        LOGGER.info(f"user client authorised — session saved ({first_name})")
    except Exception:
        LOGGER.info("user client authorised — session saved")

    # retrieve saved download params
    chat_entity = state["chat_entity"]
    is_range = state["is_range"]
    start_id = state["start_id"]
    end_id = state["end_id"]
    duration_secs = state["duration_secs"]
    param_label = state["param_label"]

    _login_state.pop(chat_id, None)

    await send_message(chat_id, f"✅ Login Successful\nLogged in as **{first_name}**\nStarting download...")

    async with _download_lock:
        await _run_download(
            chat_id, _user_client, chat_entity,
            is_range, start_id, end_id, duration_secs, param_label,
        )
