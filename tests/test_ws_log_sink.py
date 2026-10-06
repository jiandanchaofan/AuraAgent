"""Tests for gui/ws_log_sink.py::WebSocketSink — the write()-never-blocks
contract (core/logger.py's module docstring), that drain() forwards
exactly what was queued in order, that two registered connections never
see each other's events, and that broadcast-tagged event types (N14:
"schedule_result") reach every registered connection instead of just the
current one.
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
    sink.register("conn-1")
    sink.current = "conn-1"
    # No event loop running at all here -- if write() tried to await
    # anything, this would raise. put_nowait() must be all it does.
    sink.write({"event_type": "user_input", "payload": {"text": "hi"}})
    assert sink._queues["conn-1"].qsize() == 1


def test_write_with_no_current_connection_is_a_no_op():
    sink = WebSocketSink()
    sink.register("conn-1")
    # current defaults to None until something sets it.
    sink.write({"event_type": "user_input", "payload": {"text": "hi"}})
    assert sink._queues["conn-1"].qsize() == 0


@pytest.mark.asyncio
async def test_drain_forwards_events_in_order():
    sink = WebSocketSink()
    sink.register("conn-1")
    sink.current = "conn-1"
    connection = _FakeConnection()
    sink.write({"event_type": "user_input", "payload": {"text": "first"}})
    sink.write({"event_type": "final_answer", "payload": {"text": "second"}})

    drain_task = asyncio.create_task(sink.drain("conn-1", connection))
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
    sink.register("conn-1")
    sink.current = "conn-1"
    connection = _FakeConnection()
    drain_task = asyncio.create_task(sink.drain("conn-1", connection))
    await asyncio.sleep(0.01)

    sink.write({"event_type": "thought", "payload": {"text": "later"}})
    await asyncio.sleep(0.05)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    assert connection.sent == [{"event_type": "thought", "payload": {"text": "later"}}]


@pytest.mark.asyncio
async def test_two_connections_never_see_each_others_events():
    sink = WebSocketSink()
    sink.register("conn-1")
    sink.register("conn-2")
    conn1, conn2 = _FakeConnection(), _FakeConnection()
    drain1 = asyncio.create_task(sink.drain("conn-1", conn1))
    drain2 = asyncio.create_task(sink.drain("conn-2", conn2))

    sink.current = "conn-1"
    sink.write({"event_type": "thought", "payload": {"text": "for conn 1"}})
    sink.current = "conn-2"
    sink.write({"event_type": "thought", "payload": {"text": "for conn 2"}})
    await asyncio.sleep(0.05)

    for task in (drain1, drain2):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [e["payload"]["text"] for e in conn1.sent] == ["for conn 1"]
    assert [e["payload"]["text"] for e in conn2.sent] == ["for conn 2"]


@pytest.mark.asyncio
async def test_schedule_result_broadcasts_to_every_connection():
    # N14: a finished scheduled task isn't "whichever connection is
    # current"'s event -- every connected device (desktop GUI, an Auralis
    # phone) should see it.
    sink = WebSocketSink()
    sink.register("conn-1")
    sink.register("conn-2")
    conn1, conn2 = _FakeConnection(), _FakeConnection()
    drain1 = asyncio.create_task(sink.drain("conn-1", conn1))
    drain2 = asyncio.create_task(sink.drain("conn-2", conn2))

    sink.current = "conn-1"  # only conn-1 is "current" -- shouldn't matter
    sink.write({"event_type": "schedule_result", "payload": {"summary": "done"}})
    await asyncio.sleep(0.05)

    for task in (drain1, drain2):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [e["payload"]["summary"] for e in conn1.sent] == ["done"]
    assert [e["payload"]["summary"] for e in conn2.sent] == ["done"]


def test_unregister_clears_current_when_it_points_at_that_connection():
    sink = WebSocketSink()
    sink.register("conn-1")
    sink.current = "conn-1"

    sink.unregister("conn-1")

    assert sink.current is None
    # A write with nothing registered for "conn-1" anymore must not raise.
    sink.write({"event_type": "thought", "payload": {"text": "dropped"}})
