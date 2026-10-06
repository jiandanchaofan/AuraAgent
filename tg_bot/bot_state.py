"""TelegramBotState -- bundles everything tg_bot/handlers.py's handlers
need, built once in telegram_bot.py's post_init and stashed on
application.bot_data["state"]. The bot's equivalent of one GUI
connection's local state (gui/server.py's websocket_endpoint locals) --
except there is only ever ONE of these per process, never N (one chat_id,
no session-switching UI -- see tools/sessions/session_store.py's
SessionInfo and the product decision to always auto-resume the single
most-recent session).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.bootstrap import AppContext
from core.message_types import ConversationTurn
from gui.session_sink import SessionSink
from tg_bot.channel import TelegramConfirmationChannel
from tools.sessions.session_store import ChatSessionStore, SessionInfo


@dataclass
class TelegramBotState:
    ctx: AppContext
    confirmation_channel: TelegramConfirmationChannel
    session_store: ChatSessionStore
    session_sink: SessionSink
    allowed_chat_id: int | None
    active_session: SessionInfo
    history: list[ConversationTurn]
    #: Non-None + not done() means a turn is in flight -- the busy flag,
    #: same role gui/server.py's `run_tasks: set[...]` plays (a set there
    #: because N simultaneous connections each need their own; here there's
    #: only ever one chat, so a single optional Task is enough).
    run_task: asyncio.Task[None] | None = None
