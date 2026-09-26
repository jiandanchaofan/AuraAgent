"""Tests for gui/ws_log_sink.py::WebSocketSink — the write()-never-blocks
contract (core/logger.py's module docstring) and that drain() forwards
exactly what was queued, in order.
"""
from __future__ import annotations

import asyncio

import pytest

from gui.ws_log_sink import WebSocketSink


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


def test_write_is_synchronous_and_non_blocking():
    sink = WebSocketSink()
    # No event loop running at all here -- if write() tried to await
    # anything, this would raise. put_nowait() must be all it does.
    sink.write({"event_type": "user_input", "payload": {"text": "hi"}})
    assert sink._queue.qsize() == 1


@pytest.mark.asyncio
async def test_drain_forwards_events_in_order():
    sink = WebSocketSink()
    connection = _FakeConnection()
    sink.write({"event_type": "user_input", "payload": {"text": "first"}})
    sink.write({"event_type": "final_answer", "payload": {"text": "second"}})

    drain_task = asyncio.create_task(sink.drain(connection))
    await asyncio.sleep(0.05)  # let the drain loop catch up
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    assert [e["payload"]["text"] for e in connection.sent] == ["first", "second"]


@pytest.mark.asyncio
async def test_drain_delivers_events_written_after_it_starts():
    sink = WebSocketSink()
    connection = _FakeConnection()
    drain_task = asyncio.create_task(sink.drain(connection))
    await asyncio.sleep(0.01)

    sink.write({"event_type": "thought", "payload": {"text": "later"}})
    await asyncio.sleep(0.05)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    assert connection.sent == [{"event_type": "thought", "payload": {"text": "later"}}]
