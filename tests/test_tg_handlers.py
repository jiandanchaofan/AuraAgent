"""Tests for tg_bot/handlers.py -- access control (the allowed_chat_id
gate, including the "not yet configured" discovery-reply mode), the
busy-flag that stops two turns running concurrently on this bot, the
open-question disambiguation (a plain-text reply answering a pending
question must NOT be treated as a new chat turn), and the confirm/decline
button-callback parsing. `_run_turn` itself is never exercised here (it
would need a real AppContext/orchestrator) -- it's monkeypatched out where
a test needs to observe "a turn was started" without running one for real.
"""
from __future__ import annotations

import asyncio

import pytest

import tg_bot.handlers as handlers
from tg_bot.bot_state import TelegramBotState
from tg_bot.channel import TelegramConfirmationChannel


class _FakeMessage:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, *, chat_id: int | None, text: str | None = None, callback_query=None) -> None:
        self.effective_chat = _FakeChat(chat_id) if chat_id is not None else None
        self.message = _FakeMessage(text) if text is not None else None
        self.callback_query = callback_query


class _FakeCallbackQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = False
        self.edited_markup: object = "not-called"

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_reply_markup(self, reply_markup=None) -> None:
        self.edited_markup = reply_markup


class _FakeBot:
    async def send_message(self, chat_id, text, reply_markup=None):
        pass


class _FakeApplication:
    def __init__(self, state: TelegramBotState) -> None:
        self.bot_data = {"state": state}


class _FakeContext:
    def __init__(self, state: TelegramBotState) -> None:
        self.application = _FakeApplication(state)


def _state(*, allowed_chat_id: int | None) -> TelegramBotState:
    channel = TelegramConfirmationChannel(bot=_FakeBot(), chat_id=allowed_chat_id, logger=None)
    return TelegramBotState(
        ctx=None,  # never touched unless _run_turn actually runs, which these tests avoid
        confirmation_channel=channel,
        session_store=None,
        session_sink=None,
        allowed_chat_id=allowed_chat_id,
        active_session=None,
        history=[],
    )


# --- access control -----------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_allowed_chat_id_replies_with_discovery_message():
    state = _state(allowed_chat_id=None)
    update = _FakeUpdate(chat_id=999, text="hello")

    await handlers.handle_text(update, _FakeContext(state))

    assert update.message.replies == ["Your chat_id is 999. Set AURA_TELEGRAM_ALLOWED_CHAT_ID=999 in .env and restart."]
    assert state.run_task is None


@pytest.mark.asyncio
async def test_mismatched_chat_id_is_silently_ignored():
    state = _state(allowed_chat_id=123)
    update = _FakeUpdate(chat_id=999, text="hello")

    await handlers.handle_text(update, _FakeContext(state))

    assert update.message.replies == []
    assert state.run_task is None


# --- busy flag ------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_message_while_busy_gets_a_wait_reply_and_does_not_replace_the_task():
    state = _state(allowed_chat_id=123)
    original_task = asyncio.create_task(asyncio.sleep(10))
    state.run_task = original_task
    update = _FakeUpdate(chat_id=123, text="are you done yet")

    await handlers.handle_text(update, _FakeContext(state))

    assert update.message.replies == ["⏳ Still working on the previous message -- hang tight."]
    assert state.run_task is original_task
    original_task.cancel()


@pytest.mark.asyncio
async def test_a_finished_run_task_does_not_count_as_busy(monkeypatch):
    state = _state(allowed_chat_id=123)
    finished = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)
    assert finished.done()
    state.run_task = finished

    started = asyncio.Event()

    async def fake_run_turn(state_arg, text_arg):
        started.set()

    monkeypatch.setattr(handlers, "_run_turn", fake_run_turn)
    update = _FakeUpdate(chat_id=123, text="go")

    await handlers.handle_text(update, _FakeContext(state))
    await asyncio.wait_for(started.wait(), timeout=1)

    assert state.run_task is not finished


# --- open-question disambiguation -----------------------------------------


@pytest.mark.asyncio
async def test_plain_text_while_question_pending_resolves_it_instead_of_starting_a_turn(monkeypatch):
    state = _state(allowed_chat_id=123)

    async def fail_if_called(state_arg, text_arg):
        raise AssertionError("must not start a new turn while a question is pending")

    monkeypatch.setattr(handlers, "_run_turn", fail_if_called)

    question_task = asyncio.create_task(state.confirmation_channel.ask_open_question("what's your name?"))
    await asyncio.sleep(0.01)

    update = _FakeUpdate(chat_id=123, text="James")
    await handlers.handle_text(update, _FakeContext(state))

    answer = await question_task
    assert answer == "James"
    assert state.run_task is None


# --- handle_callback -------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_resolves_approval_and_clears_buttons():
    state = _state(allowed_chat_id=123)
    # Seed a pending future directly, the way confirm() would have left it.
    request_id = "abc123"
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    state.confirmation_channel._pending[request_id] = future

    query = _FakeCallbackQuery(data=f"confirm:{request_id}:1")
    update = _FakeUpdate(chat_id=123, callback_query=query)

    await handlers.handle_callback(update, _FakeContext(state))

    assert query.answered is True
    assert query.edited_markup is None
    assert future.done() and future.result() is True


@pytest.mark.asyncio
async def test_handle_callback_resolves_decline():
    state = _state(allowed_chat_id=123)
    request_id = "def456"
    future = asyncio.get_running_loop().create_future()
    state.confirmation_channel._pending[request_id] = future

    query = _FakeCallbackQuery(data=f"confirm:{request_id}:0")
    update = _FakeUpdate(chat_id=123, callback_query=query)

    await handlers.handle_callback(update, _FakeContext(state))

    assert future.result() is False


@pytest.mark.asyncio
async def test_handle_callback_from_unauthorized_chat_still_acks_but_does_not_resolve():
    state = _state(allowed_chat_id=123)
    request_id = "ghi789"
    future = asyncio.get_running_loop().create_future()
    state.confirmation_channel._pending[request_id] = future

    query = _FakeCallbackQuery(data=f"confirm:{request_id}:1")
    update = _FakeUpdate(chat_id=999, callback_query=query)

    await handlers.handle_callback(update, _FakeContext(state))

    assert query.answered is True
    assert not future.done()
