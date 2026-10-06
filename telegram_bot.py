"""AuraAgent Telegram bot entrypoint -- the third frontend.

Same composition root as main.py (CLI) and gui/server.py (GUI): builds an
AuraLogger + a ConfirmationChannel, calls core/bootstrap.py::build_app_context(),
then drives one coroutine per turn: ctx.orchestrator.run(text, history=history)
(see tg_bot/handlers.py::_run_turn). Runs via long-polling -- no public HTTPS
endpoint/Cloudflare Tunnel needed (unlike the Auralis/GUI remote-access path),
which is fine at personal-chat message volume.

This is a fully independent process with its OWN build_app_context() call and
OWN AppContext (own run_lock, own workspace_root/active_project pointers) --
exactly the same relationship CLI and GUI already have to each other when
both happen to run at once. It shares only durable, disk-backed state with
them (MemoryStore, ProjectStore, GraphStore, notes files, etc. -- anything
addressed by a Settings path); its own chat history lives in a SEPARATE
ChatSessionStore directory (telegram_chat_sessions_dir), never touched by
another frontend.
"""
from __future__ import annotations

import asyncio

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters

from cli import service
from config.settings import load_settings
from core.bootstrap import build_app_context
from core.logger import AuraLogger, JSONLSink, TerminalSink
from gui.session_sink import SessionSink
from tg_bot.bot_state import TelegramBotState
from tg_bot.channel import TelegramConfirmationChannel
from tg_bot.handlers import handle_callback, handle_text, on_error
from tg_bot.sink import TelegramLogSink
from tools.sessions.session_store import ChatSessionStore


async def _post_init(application) -> None:
    settings = application.bot_data["settings"]
    sink = TelegramLogSink()
    session_store = ChatSessionStore(settings.telegram_chat_sessions_dir)
    session_sink = SessionSink(session_store)
    logger = AuraLogger([TerminalSink(), JSONLSink(settings.logs_dir), sink, session_sink])
    confirmation_channel = TelegramConfirmationChannel(application.bot, settings.telegram_allowed_chat_id, logger)

    ctx = await build_app_context(settings, confirmation_channel, logger)

    existing = await session_store.list_sessions()
    active_session = existing[0] if existing else await session_store.create_session()
    session_sink.active_session_id = active_session.id
    leader_name = ctx.agent_registry.leader.name
    history = service.history_from_events(session_store.read_events(active_session.id), leader_name)

    application.bot_data["state"] = TelegramBotState(
        ctx=ctx,
        confirmation_channel=confirmation_channel,
        session_store=session_store,
        session_sink=session_sink,
        allowed_chat_id=settings.telegram_allowed_chat_id,
        active_session=active_session,
        history=history,
    )
    if settings.telegram_allowed_chat_id is not None:
        application.bot_data["drain_task"] = asyncio.create_task(
            sink.drain(application.bot, settings.telegram_allowed_chat_id)
        )


async def _post_shutdown(application) -> None:
    drain_task = application.bot_data.get("drain_task")
    if drain_task is not None:
        drain_task.cancel()
    state: TelegramBotState | None = application.bot_data.get("state")
    if state is not None:
        await state.ctx.aclose()


def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["settings"] = settings
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(on_error)

    application.run_polling()  # the one blocking call -- owns its own event loop, never nest asyncio.run() around this


if __name__ == "__main__":
    main()
