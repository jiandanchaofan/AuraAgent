"""WebSocketSink — the GUI's LogSink implementation, and also the single
outgoing channel WebSocketConfirmationChannel (gui/ws_channel.py) reuses
for confirmation_request/open_question_request messages, so a GUI client
sees everything (white-box events AND requests for its input) as one
ordered stream rather than two separately-plumbed channels.

One queue PER CONNECTION (`register`/`unregister`), not one shared queue —
with more than one WebSocket connection open (the desktop GUI and a
connected Auralis phone, or two browser tabs), a single shared queue would
let whichever connection's drain() task happens to call `.get()` next
steal events meant for a different connection. `current` says which
connection's queue write() should route to right now; gui/server.py sets
it immediately before any action that will produce events (an incoming
chat turn, a session switch), always while holding ctx.run_lock (see that
module's docstring) so one connection's actions can never be misrouted to
a different connection's queue if two overlap in time.

`broadcast()` is the one deliberate exception: a small set of event types
(currently just "schedule_result" -- see core/logger.py and
tools/scheduler/scheduler_loop.py) are NOT "this one connection's live
turn," they're a background event every connected device should see
regardless of which chat it has open -- so write() special-cases those
event types to go to every registered queue instead of just `current`'s.

write() must never await (see core/logger.py's module docstring —
AsyncReActEngine calls log_*() synchronously), so this only ever does the
non-blocking asyncio.Queue.put_nowait(). The actual network send happens
in drain(), a coroutine gui/server.py runs as a background task for the
lifetime of one WebSocket connection. Events written before `current`
names a registered connection are simply dropped — there is always at
least one registered connection by the time anything would write (see
gui/server.py's connect-time sync), so this is only ever a defensive
no-op, not a real code path.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from core.logger import LogSink

#: Event types that go to every connected device, not just whichever
#: connection is `current` right now -- see the module docstring.
_BROADCAST_EVENT_TYPES = {"schedule_result"}


class _SendsJSON(Protocol):
    async def send_json(self, data: Any) -> None: ...


class WebSocketSink(LogSink):
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self.current: str | None = None

    def register(self, connection_id: str) -> None:
        self._queues[connection_id] = asyncio.Queue()

    def unregister(self, connection_id: str) -> None:
        self._queues.pop(connection_id, None)
        if self.current == connection_id:
            self.current = None

    def write(self, event: dict[str, Any]) -> None:
        if event.get("event_type") in _BROADCAST_EVENT_TYPES:
            self.broadcast(event)
            return
        if self.current is None:
            return
        queue = self._queues.get(self.current)
        if queue is not None:
            queue.put_nowait(event)

    def broadcast(self, event: dict[str, Any]) -> None:
        """Delivers `event` to every currently-registered connection,
        regardless of `current` -- see the module docstring for why a
        background event like a completed scheduled task is routed
        differently from a live turn's own events."""
        for queue in self._queues.values():
            queue.put_nowait(event)

    async def drain(self, connection_id: str, connection: _SendsJSON) -> None:
        """Runs until cancelled, forwarding every event queued for
        `connection_id` to `connection` (a starlette WebSocket in practice
        — typed as a minimal Protocol here so this stays testable with a
        plain fake). gui/server.py cancels this task when the connection
        closes, and unregisters the same connection_id."""
        queue = self._queues[connection_id]
        while True:
            event = await queue.get()
            await connection.send_json(event)
