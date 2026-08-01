"""
Rich Message Streaming Utilities for Telegram Bot.

Provides Telethon integration for Telegram's new rich message features:
- Thinking/draft animation in private chats (SetTypingRequest + RichMessageDraftAction)
- Streaming content delivery (block-by-block edits)
- Rich markdown messages with headings, tables, lists, etc.

Based on Telegram's rich message API (InputRichMessageMarkdown / InputRichMessageHTML).
"""

import asyncio
from telethon import helpers as th
from telethon.tl import functions, types

from bot import ItsMrULPBot
from helpers.logger import LOGGER

# ── configurable delays ──
STREAM_DELAY = 0.3       # seconds between content blocks during streaming
THINKING_DELAY = 0.6     # seconds between thinking animation steps

# ── default thinking animation steps ──
DEFAULT_THINKING_STEPS = [
    "🔍 Analyzing data...",
    "🧠 Processing information...",
    "✍️ Preparing response...",
]

SUMMARY_THINKING_STEPS = [
    "🔍 Scanning database files...",
    "🧮 Counting domains and entries...",
    "📊 Building Top 5 domains report...",
]

SEARCH_THINKING_STEPS = [
    "🔍 Searching database for keyword...",
    "📋 Filtering and deduplicating results...",
    "📦 Packaging results for delivery...",
]


# ═══════════════════════════════════════════════════════════════════
# internal helpers
# ═══════════════════════════════════════════════════════════════════

def _new_random_id() -> int:
    """Generate a random ID for draft actions."""
    return abs(th.generate_random_long())


async def _send_rich_draft(event, html: str, draft_id: int) -> None:
    """
    Send a rich draft (thinking indicator / streaming preview) in private chats.

    Uses SetTypingRequest with InputSendMessageRichMessageDraftAction
    to show a live preview while the bot "thinks".
    """
    try:
        await ItsMrULPBot(functions.messages.SetTypingRequest(
            peer=event.chat_id,
            action=types.InputSendMessageRichMessageDraftAction(
                random_id=draft_id,
                rich_message=types.InputRichMessageHTML(html=html),
            ),
        ))
    except Exception as e:
        LOGGER.debug(f"Rich draft send failed: {e}")


async def _send_rich_markdown(peer, markdown: str):
    """
    Send a final rich message with markdown formatting.

    Returns the sent Message object (extracted from Updates), or None on failure.
    """
    try:
        result = await ItsMrULPBot(functions.messages.SendMessageRequest(
            peer=peer,
            message=markdown[:4000],
            random_id=_new_random_id(),
            rich_message=types.InputRichMessageMarkdown(markdown=markdown),
        ))
        # Extract the sent message from the Updates response
        for update in result.updates:
            if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
                return update.message
        return None
    except Exception as e:
        LOGGER.error(f"Rich message send failed: {e}")
        return None


async def _edit_rich_markdown(peer, msg_id: int, markdown: str):
    """
    Edit an existing message with rich markdown formatting.

    Returns the Updates response, or None on failure.
    """
    try:
        return await ItsMrULPBot(functions.messages.EditMessageRequest(
            peer=peer,
            id=msg_id,
            message=markdown[:4000],
            rich_message=types.InputRichMessageMarkdown(markdown=markdown),
        ))
    except Exception as e:
        LOGGER.debug(f"Rich edit failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# public API
# ═══════════════════════════════════════════════════════════════════

async def send_rich_message(chat_id: int, text: str):
    """
    Send a single rich-formatted markdown message.

    Shortcut for commands that don't need streaming.
    Returns the sent Message, or None.
    """
    from telethon.tl.types import InputPeerUser, InputPeerChat, InputPeerChannel

    # Resolve peer from chat_id  (negative = channel/group, positive = user)
    if chat_id < 0:
        # For channels/groups, we need to get the access_hash from the dialog
        # Fallback: try sending without access_hash (works for known dialogs)
        try:
            entity = await ItsMrULPBot.get_input_entity(chat_id)
        except Exception:
            # Fallback: use plain integer (may work for some cases)
            entity = chat_id
    else:
        entity = chat_id

    return await _send_rich_markdown(entity, text)


async def edit_rich_message(chat_id: int, msg_id: int, text: str):
    """
    Edit an existing message with rich-formatted markdown.
    """
    from telethon.tl.types import InputPeerUser, InputPeerChat, InputPeerChannel

    try:
        entity = await ItsMrULPBot.get_input_entity(chat_id)
    except Exception:
        entity = chat_id

    return await _edit_rich_markdown(entity, msg_id, text)


async def stream_rich_response(
    event,
    text: str,
    thinking_steps: list = None,
    stream_delay: float = None,
):
    """
    Stream a rich response to the user with thinking animation.

    **Private chats:**
        1. Show thinking/draft animation (cycling through ``thinking_steps``)
        2. Stream the content block-by-block as live previews
        3. Deliver the final rich message

    **Groups / channels:**
        1. Send a placeholder *"Processing..."* message
        2. Stream the content via block-by-block edits
        3. Final edit with complete content

    Parameters
    ----------
    event : NewMessage.Event
        The incoming message event.
    text : str
        Full markdown text to display.
    thinking_steps : list of str, optional
        HTML strings for the thinking animation (private chats only).
        Defaults to ``DEFAULT_THINKING_STEPS``.
    stream_delay : float, optional
        Seconds between streaming blocks. Defaults to ``STREAM_DELAY``.

    Returns
    -------
    Message | None
        The final sent/edited message, or None on failure.
    """
    if thinking_steps is None:
        thinking_steps = DEFAULT_THINKING_STEPS
    if stream_delay is None:
        stream_delay = STREAM_DELAY

    peer = await event.get_input_chat()
    is_private = event.is_private

    if is_private:
        # ── Phase 1: thinking animation ──
        draft_id = _new_random_id()
        for step in thinking_steps:
            await _send_rich_draft(event, step, draft_id)
            await asyncio.sleep(THINKING_DELAY)

        # ── Phase 2: stream content block by block ──
        blocks = _split_into_blocks(text)
        if blocks:
            for i in range(1, len(blocks) + 1):
                chunk = "\n\n".join(blocks[:i])
                await _send_rich_draft(event, chunk, draft_id)
                await asyncio.sleep(stream_delay)

        # ── Phase 3: final rich message ──
        return await _send_rich_markdown(peer, text)

    else:
        # ── Group / channel: placeholder → stream edits → final edit ──
        from helpers.botutils import send_message
        message = await send_message(event.chat_id, "⏳ **Processing...**")
        if not message:
            return None

        blocks = _split_into_blocks(text)
        if blocks:
            for i in range(1, len(blocks) + 1):
                chunk = "\n\n".join(blocks[:i])
                await _edit_rich_markdown(peer, message.id, chunk)
                await asyncio.sleep(stream_delay)

        # Final edit with complete content
        await _edit_rich_markdown(peer, message.id, text)
        return message


async def stream_rich_from_cache(
    event,
    cache: dict,
    build_output_fn,
    thinking_steps: list = None,
):
    """
    Convenience wrapper: check cache, build output, and stream it.

    Parameters
    ----------
    event : NewMessage.Event
    cache : dict or None
        The cached data. If None, sends an error message.
    build_output_fn : callable
        Function that takes ``cache`` and returns a formatted markdown string.
    thinking_steps : list of str, optional

    Returns
    -------
    Message | None
    """
    from helpers.botutils import send_message, edit_message

    if cache is None:
        return await send_message(
            event.chat_id, "**❌ No cached data available — try again later**"
        )

    # Build the output text
    try:
        output = build_output_fn(cache)
    except Exception as e:
        LOGGER.error(f"Failed to build rich output: {e}")
        return await send_message(event.chat_id, "**❌ Failed to generate report**")

    if not output:
        return await send_message(
            event.chat_id,
            "**❌ No data available**\nTry again later (cache refreshes every 2 hours)."
        )

    return await stream_rich_response(event, output, thinking_steps=thinking_steps)


# ═══════════════════════════════════════════════════════════════════
# utilities
# ═══════════════════════════════════════════════════════════════════

def _split_into_blocks(text: str) -> list:
    """
    Split markdown text into streamable blocks (by double-newline paragraphs).

    Preserves code blocks (```...```), tables, and other multi-line structures
    that shouldn't be split mid-block.
    """
    # Simple approach: split by double newline, keep non-empty blocks
    blocks = []
    in_code_block = False
    current = []

    for line in text.split("\n"):
        stripped = line.strip()

        # Track code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            current.append(line)
            if not in_code_block:
                # End of code block
                blocks.append("\n".join(current))
                current = []
            continue

        if in_code_block:
            current.append(line)
            continue

        # Empty line = block separator
        if stripped == "":
            if current:
                blocks.append("\n".join(current))
                current = []
            continue

        current.append(line)

    # Flush remaining
    if current:
        blocks.append("\n".join(current))

    return blocks
