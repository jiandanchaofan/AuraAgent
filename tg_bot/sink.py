"""TelegramLogSink -- the Telegram bot's LogSink.

Unlike WebSocketSink (gui/ws_log_sink.py), there is exactly ONE destination
ever: the single authorized chat_id (see config/settings.py's
AURA_TELEGRAM_ALLOWED_CHAT_ID -- this is a personal single-user assistant,
not a public bot). So the per-connection multi-queue/`current`-pointer/
`broadcast()` machinery WebSocketSink needs (to route each event to the
right one of N simultaneous browser tabs/devices) has nothing to do here --
there is only ever one "connection," fixed for the process's whole
lifetime, so one plain asyncio.Queue is both correct and considerably
simpler than copying WebSocketSink's per-connection routing.

Also unlike the GUI (which streams every white-box event -- thought/
tool_call/observation -- live to the browser), forwarding every one of
those as a SEPARATE Telegram message would be spammy chat noise (confirmed
with the user). Only final_answer/error/schedule_result become real
messages; tool_call names are accumulated and folded into a single
"🔧 used: a, b" line appended to the next forwarded message instead of one
message per call.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from core.logger import LogSink

_FORWARDED_EVENT_TYPES = {"final_answer", "error", "schedule_result"}
#: Telegram's sendMessage hard cap is 4096 UTF-16 code units; stay under it
#: with margin for the "🔧 used: ..." suffix this sink may append.
_TELEGRAM_MESSAGE_LIMIT = 4000


class _SendsMessage(Protocol):
    async def send_message(self, chat_id: int, text: str) -> Any: ...


class TelegramLogSink(LogSink):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._tool_calls: list[str] = []

    def write(self, event: dict[str, Any]) -> None:
        # Never awaits -- see core/logger.py's module docstring.
        if event["event_type"] == "tool_call":
            self._tool_calls.append(event["payload"]["tool_name"])
            return
        if event["event_type"] not in _FORWARDED_EVENT_TYPES:
            return
        self._queue.put_nowait(event)

    def _render(self, event: dict[str, Any]) -> str:
        if event["event_type"] == "final_answer":
            text = event["payload"]["text"]
        elif event["event_type"] == "error":
            text = f"⚠️ {event['payload']['message']}"
        else:  # schedule_result
            text = f"✅ Scheduled task finished: {event['payload']['task']}\n{event['payload']['summary']}"
        if self._tool_calls:
            text = f"{text}\n\n🔧 used: {', '.join(self._tool_calls)}"
            self._tool_calls = []
        return text

    async def drain(self, bot: _SendsMessage, chat_id: int) -> None:
        """Runs until cancelled -- the one background task that actually
        calls the Telegram send-message API, bridging write()'s sync
        enqueue to a real async HTTP call, the same seam WebSocketSink.drain()
        fills for the GUI."""
        while True:
            event = await self._queue.get()
            for chunk in _chunk_text(self._render(event)):
                await bot.send_message(chat_id=chat_id, text=chunk)


def _chunk_text(text: str, limit: int = _TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Splits on paragraph boundaries where possible, falling back to a
    hard split -- Telegram rejects sendMessage calls over ~4096 chars
    outright, so a long final_answer (e.g. a generated report) must never
    be sent as one oversized call."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
