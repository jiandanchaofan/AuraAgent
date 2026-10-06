"""Telegram message/callback handlers -- the bot's equivalent of
gui/server.py's websocket_endpoint message loop, adapted to
python-telegram-bot's handler-dispatch model instead of one
`while True: receive_text()` loop.

Busy-flag: a turn must not run concurrently with another on this bot (same
reasoning as gui/server.py's run_tasks check) -- state.run_task non-None and
not done() means "still working"; a second incoming message gets a short
"⏳ still working" reply rather than being silently dropped or queued.

Access control: every handler checks chat_id against state.allowed_chat_id
BEFORE doing anything else. If unset, the bot's ONLY behavior is replying
with "your chat_id is <N>" -- it never reaches the orchestrator until
configured (see config/settings.py's AURA_TELEGRAM_ALLOWED_CHAT_ID). If set
and the incoming chat_id doesn't match, the message is silently ignored (no
reply at all) -- unlike the "unset" case, acknowledging the bot's existence
to a stranger who isn't the owner isn't worth doing.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from cli import service
from tg_bot.bot_state import TelegramBotState


def _get_state(context: ContextTypes.DEFAULT_TYPE) -> TelegramBotState:
    return context.application.bot_data["state"]


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    if update.message is None or update.message.text is None or update.effective_chat is None:
        return
    chat_id = update.effective_chat.id

    if state.allowed_chat_id is None:
        await update.message.reply_text(
            f"Your chat_id is {chat_id}. Set AURA_TELEGRAM_ALLOWED_CHAT_ID={chat_id} in .env and restart."
        )
        return
    if chat_id != state.allowed_chat_id:
        return  # not the owner -- ignore silently

    text = update.message.text
    # Disambiguation (see tg_bot/channel.py's module docstring): if a
    # question is pending, THIS message is its answer, not a new chat turn.
    if state.confirmation_channel.pending_question_future is not None:
        state.confirmation_channel.resolve_question(text)
        return

    if state.run_task is not None and not state.run_task.done():
        await update.message.reply_text("⏳ Still working on the previous message -- hang tight.")
        return

    state.run_task = asyncio.create_task(_run_turn(state, text))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    query = update.callback_query
    if query is None or query.data is None or update.effective_chat is None:
        return
    chat_id = update.effective_chat.id
    await query.answer()  # always ack the tap, even if unauthorized/stale
    if state.allowed_chat_id is None or chat_id != state.allowed_chat_id:
        return
    _, request_id, approved_flag = query.data.split(":", 2)
    state.confirmation_channel.resolve_confirmation(request_id, approved_flag == "1")
    try:
        await query.edit_message_reply_markup(reply_markup=None)  # remove the buttons once decided
    except Exception:  # noqa: BLE001 -- cosmetic only, never worth failing the resolve over
        pass


async def _run_turn(state: TelegramBotState, text: str) -> None:
    """Mirrors gui/server.py's _run_orchestrator(): holds ctx.run_lock for
    the full turn (shared with the scheduler and any other frontend running
    in the same process), re-syncs the active directory defensively, then
    drives the one shared orchestrator.run() call. Unlike the GUI, there is
    no _maybe_autoname_session call -- Telegram has no session-switching UI
    to show a title in, so naming a session here would be dead code."""
    ctx = state.ctx
    try:
        async with ctx.run_lock:
            session = await state.session_store.get_session(state.active_session.id)
            if session is not None:
                try:
                    await service.sync_active_directory(
                        ctx.cli_context, project_slug=session.project_slug, directory_override=session.directory
                    )
                except ValueError as exc:
                    ctx.logger.log_error(-1, f"Could not resolve this chat's directory: {exc}")
            await ctx.orchestrator.run(text, history=state.history)
    except Exception as exc:  # noqa: BLE001 -- surfaced via logger -> TelegramLogSink, mirrors main.py's REPL
        ctx.logger.log_error(-1, f"{type(exc).__name__}: {exc}")
        return
    await state.session_store.touch(state.active_session.id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB's own error handler hook -- catches anything raised inside a
    handler (e.g. a transient Telegram network error) so it's logged
    instead of silently crashing that one update's dispatch."""
    state: TelegramBotState | None = context.application.bot_data.get("state")
    if state is not None:
        state.ctx.logger.log_error(-1, f"Telegram handler error: {context.error}")
