"""Tests for gui/ws_channel.py::WebSocketConfirmationChannel — that
confirm()/ask_open_question() write a request onto the shared
WebSocketSink queue and block until resolve() is called with a matching
request_id, mirroring the same interface contract
tests/test_terminal_channel.py verifies for TerminalConfirmationChannel.
"""
from __future__ import annotations

import asyncio

import pytest

from confirmation.base import ConfirmationRequest
from core.logger import AuraLogger
from gui.ws_channel import WebSocketConfirmationChannel
from gui.ws_log_sink import WebSocketSink


def _drain_one(sink: WebSocketSink) -> dict:
    return sink._queues[sink.current].get_nowait()


def _registered(sink: WebSocketSink) -> WebSocketSink:
    """Every write() in these tests needs a registered+current connection
    (N14) -- this is the single-connection fake id used throughout."""
    sink.register("conn-1")
    sink.current = "conn-1"
    return sink


@pytest.mark.asyncio
async def test_confirm_writes_a_request_event_and_awaits_resolution():
    sink = _registered(WebSocketSink())
    logger = AuraLogger([sink])
    channel = WebSocketConfirmationChannel(sink, logger)

    task = asyncio.create_task(
        channel.confirm(ConfirmationRequest(tool_name="delete_file", arguments={}, reason="ok?", risk_level="destructive"))
    )
    await asyncio.sleep(0.01)  # let confirm() reach the `await future` point

    event = _drain_one(sink)
    assert event["event_type"] == "confirmation_request"
    assert event["payload"] == {"tool_name": "delete_file", "arguments": {}, "reason": "ok?", "risk_level": "destructive"}
    assert not task.done()  # still waiting on the human

    channel.resolve(event["request_id"], True)
    approved = await task

    assert approved is True


@pytest.mark.asyncio
async def test_confirm_decision_is_logged_after_resolution():
    sink = WebSocketSink()
    events: list[dict] = []

    class _RecordingSink(WebSocketSink):
        def write(self, event):  # type: ignore[override]
            events.append(event)
            super().write(event)

    recording = _registered(_RecordingSink())
    logger = AuraLogger([recording])
    channel = WebSocketConfirmationChannel(recording, logger)

    task = asyncio.create_task(
        channel.confirm(ConfirmationRequest(tool_name="x", arguments={}, reason="ok?"))
    )
    await asyncio.sleep(0.01)
    request_event = _drain_one(recording)
    channel.resolve(request_event["request_id"], False)
    await task

    confirmation_events = [e for e in events if e["event_type"] == "confirmation"]
    assert len(confirmation_events) == 1
    assert confirmation_events[0]["payload"] == {"prompt": "ok?", "decision": False}


@pytest.mark.asyncio
async def test_ask_open_question_writes_a_request_and_returns_the_answer():
    sink = _registered(WebSocketSink())
    logger = AuraLogger([sink])
    channel = WebSocketConfirmationChannel(sink, logger)

    task = asyncio.create_task(channel.ask_open_question("What's the API key?"))
    await asyncio.sleep(0.01)

    event = _drain_one(sink)
    assert event["event_type"] == "open_question_request"
    assert event["payload"] == {"prompt": "What's the API key?"}

    channel.resolve(event["request_id"], "sk-secret-value")
    answer = await task

    assert answer == "sk-secret-value"


@pytest.mark.asyncio
async def test_ask_open_question_answer_is_never_logged():
    sink = WebSocketSink()
    logged: list[dict] = []

    class _RecordingSink(WebSocketSink):
        def write(self, event):  # type: ignore[override]
            logged.append(event)
            super().write(event)

    recording = _registered(_RecordingSink())
    logger = AuraLogger([recording])
    channel = WebSocketConfirmationChannel(recording, logger)

    task = asyncio.create_task(channel.ask_open_question("secret?"))
    await asyncio.sleep(0.01)
    request_event = _drain_one(recording)
    channel.resolve(request_event["request_id"], "super-secret")
    await task

    assert all("super-secret" not in str(e) for e in logged)


@pytest.mark.asyncio
async def test_resolve_with_unknown_request_id_is_a_no_op():
    sink = WebSocketSink()
    logger = AuraLogger([sink])
    channel = WebSocketConfirmationChannel(sink, logger)

    channel.resolve("nonexistent-id", True)  # must not raise
