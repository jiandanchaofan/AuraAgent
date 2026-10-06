"""Tests for tg_bot/channel.py::TelegramConfirmationChannel -- mirrors
tests/test_ws_channel.py's shape for the WebSocket equivalent, adapted for
the fact that requests are sent directly via a fake bot's send_message()
rather than through a shared outgoing queue.
"""
from __future__ import annotations

import asyncio

import pytest

from confirmation.base import ConfirmationRequest
from core.logger import AuraLogger
from tg_bot.channel import TelegramConfirmationChannel


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


@pytest.mark.asyncio
async def test_confirm_sends_a_message_with_buttons_and_awaits_resolution():
    bot = _FakeBot()
    logger = AuraLogger([])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    task = asyncio.create_task(
        channel.confirm(ConfirmationRequest(tool_name="delete_file", arguments={}, reason="ok?", risk_level="destructive"))
    )
    await asyncio.sleep(0.01)  # let confirm() reach the `await future` point

    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 123
    assert "ok?" in bot.sent[0]["text"]
    assert bot.sent[0]["reply_markup"] is not None
    assert not task.done()  # still waiting on the human

    request_id = channel._pending.copy().popitem()[0]
    channel.resolve_confirmation(request_id, True)
    approved = await task

    assert approved is True


@pytest.mark.asyncio
async def test_confirm_decision_is_logged_after_resolution():
    bot = _FakeBot()
    events: list[dict] = []

    class _RecordingSink:
        def write(self, event):
            events.append(event)

    logger = AuraLogger([_RecordingSink()])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    task = asyncio.create_task(channel.confirm(ConfirmationRequest(tool_name="x", arguments={}, reason="ok?")))
    await asyncio.sleep(0.01)
    request_id = next(iter(channel._pending))
    channel.resolve_confirmation(request_id, False)
    await task

    confirmation_events = [e for e in events if e["event_type"] == "confirmation"]
    assert len(confirmation_events) == 1
    assert confirmation_events[0]["payload"] == {"prompt": "ok?", "decision": False}


@pytest.mark.asyncio
async def test_resolve_confirmation_with_unknown_request_id_is_a_no_op():
    bot = _FakeBot()
    logger = AuraLogger([])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    channel.resolve_confirmation("nonexistent-id", True)  # must not raise


@pytest.mark.asyncio
async def test_ask_open_question_sends_prompt_and_sets_pending_future_until_resolved():
    bot = _FakeBot()
    logger = AuraLogger([])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    task = asyncio.create_task(channel.ask_open_question("What's the API key?"))
    await asyncio.sleep(0.01)

    assert bot.sent[-1]["text"] == "❓ What's the API key?"
    assert channel.pending_question_future is not None

    channel.resolve_question("sk-secret-value")
    answer = await task

    assert answer == "sk-secret-value"
    assert channel.pending_question_future is None  # cleared in the finally


@pytest.mark.asyncio
async def test_ask_open_question_answer_is_never_logged():
    bot = _FakeBot()
    logged: list[dict] = []

    class _RecordingSink:
        def write(self, event):
            logged.append(event)

    logger = AuraLogger([_RecordingSink()])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    task = asyncio.create_task(channel.ask_open_question("secret?"))
    await asyncio.sleep(0.01)
    channel.resolve_question("super-secret")
    await task

    assert all("super-secret" not in str(e) for e in logged)


@pytest.mark.asyncio
async def test_resolve_question_with_no_pending_question_is_a_no_op():
    bot = _FakeBot()
    logger = AuraLogger([])
    channel = TelegramConfirmationChannel(bot, 123, logger)

    channel.resolve_question("too late")  # must not raise
