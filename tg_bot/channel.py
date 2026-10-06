"""TelegramConfirmationChannel -- the Telegram bot's ConfirmationChannel.

Same pending-Futures pattern as gui/ws_channel.py::WebSocketConfirmationChannel,
except requests are sent directly via bot.send_message() (there's only one
chat, so there's no shared outgoing queue to route through the way
WebSocketConfirmationChannel reuses WebSocketSink's queue).

ask_open_question() is handled differently from confirm(): a Telegram plain-
text reply carries no reliable "which message is this answering" link (unlike
a button tap's callback_data, which carries the request_id). So this keeps a
single-slot `pending_question_future` instead of a dict keyed by request_id --
tg_bot/handlers.py::handle_text checks it BEFORE routing an incoming plain-text
message into a new chat turn. A dict isn't needed because there's only ever
one global ctx.run_lock, so at most one question can be pending at a time.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.logger import AuraLogger


class _SendsMessage(Protocol):
    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> Any: ...


class TelegramConfirmationChannel(ConfirmationChannel):
    def __init__(self, bot: _SendsMessage, chat_id: int | None, logger: AuraLogger) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._logger = logger
        self._pending: dict[str, asyncio.Future[bool]] = {}
        #: Non-None while ask_open_question() is awaiting the human's NEXT
        #: plain-text message -- see module docstring.
        self.pending_question_future: asyncio.Future[str] | None = None

    def resolve_confirmation(self, request_id: str, approved: bool) -> None:
        """Called from tg_bot/handlers.py::handle_callback. A no-op for an
        unknown/already-resolved request_id (e.g. a stale button tap after
        a restart) -- same safety as WebSocketConfirmationChannel.resolve()."""
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(approved)

    def resolve_question(self, answer: str) -> None:
        if self.pending_question_future is not None and not self.pending_question_future.done():
            self.pending_question_future.set_result(answer)

    async def confirm(self, request: ConfirmationRequest) -> bool:
        request_id = uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"confirm:{request_id}:1"),
                    InlineKeyboardButton("❌ Decline", callback_data=f"confirm:{request_id}:0"),
                ]
            ]
        )
        text = f"⚠️ {request.reason}\n\nTool: {request.tool_name}\nRisk: {request.risk_level}"
        await self._bot.send_message(chat_id=self._chat_id, text=text, reply_markup=keyboard)
        decision = await future
        self._logger.log_confirmation(-1, request.reason, decision)
        return decision

    async def ask_open_question(self, prompt: str) -> str:
        # Deliberately not logged -- same reasoning as
        # TerminalConfirmationChannel/WebSocketConfirmationChannel's own
        # ask_open_question(): this is the channel propose_mcp_server uses
        # to collect secret env-var values, which must never reach
        # logs/session-*.jsonl.
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending_question_future = future
        await self._bot.send_message(chat_id=self._chat_id, text=f"❓ {prompt}")
        try:
            return await future
        finally:
            self.pending_question_future = None
