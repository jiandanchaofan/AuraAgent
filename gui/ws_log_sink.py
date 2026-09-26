"""WebSocketSink — the GUI's LogSink implementation, and also the single
outgoing channel WebSocketConfirmationChannel (gui/ws_channel.py) reuses
for confirmation_request/open_question_request messages, so a GUI client
sees everything (white-box events AND requests for its input) as one
ordered stream rather than two separately-plumbed channels.

write() must never await (see core/logger.py's module docstring —
AsyncReActEngine calls log_*() synchronously), so this only ever does the
non-blocking asyncio.Queue.put_nowait(). The actual network send happens
in drain(), a coroutine gui/server.py runs as a background task for the
lifetime of one WebSocket connection. Events written before any client is
connected simply accumulate in the queue and get delivered as soon as
one attaches — fine for a single-user local app.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from core.logger import LogSink


class _SendsJSON(Protocol):
    async def send_json(self, data: Any) -> None: ...


class WebSocketSink(LogSink):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def write(self, event: dict[str, Any]) -> None:
        self._queue.put_nowait(event)

    async def drain(self, connection: _SendsJSON) -> None:
        """Runs until cancelled, forwarding every queued event to
        `connection` (a starlette WebSocket in practice — typed as a
        minimal Protocol here so this stays testable with a plain fake).
        gui/server.py cancels this task when the connection closes."""
        while True:
            event = await self._queue.get()
            await connection.send_json(event)
