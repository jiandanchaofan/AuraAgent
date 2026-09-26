"""WebSocketConfirmationChannel — the GUI's ConfirmationChannel
implementation. Same interface as TerminalConfirmationChannel
(confirmation/terminal_channel.py); the only thing that changes between
CLI and GUI is how a human is actually asked.

Outgoing requests are written onto the SAME WebSocketSink queue every
white-box event already flows through (see gui/ws_log_sink.py) — a
confirmation_request/open_question_request is just one more event type
on that one ordered stream, not a second channel. Incoming answers arrive
over the WebSocket's own receive loop (gui/server.py) and get routed here
via resolve(), keyed by the request_id this class generates — the classic
"pending Futures dict" pattern for turning an async request/response
protocol over a single duplex connection into something confirm()/
ask_open_question() can simply `await`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.logger import AuraLogger
from gui.ws_log_sink import WebSocketSink


class WebSocketConfirmationChannel(ConfirmationChannel):
    def __init__(self, sink: WebSocketSink, logger: AuraLogger) -> None:
        self._sink = sink
        self._logger = logger
        self._pending: dict[str, asyncio.Future[Any]] = {}

    def resolve(self, request_id: str, value: Any) -> None:
        """Called by gui/server.py's WebSocket receive loop when a
        confirmation_response/open_question_response message arrives. A
        no-op (not an error) for an unknown/already-resolved request_id —
        e.g. a stale message from a client that reconnected."""
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(value)

    async def confirm(self, request: ConfirmationRequest) -> bool:
        request_id = uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._sink.write(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event_type": "confirmation_request",
                "request_id": request_id,
                "payload": {
                    "tool_name": request.tool_name,
                    "arguments": request.arguments,
                    "reason": request.reason,
                    "risk_level": request.risk_level,
                },
            }
        )
        decision = await future
        # Same convention TerminalConfirmationChannel uses: turn/agent_name
        # aren't part of ConfirmationRequest today, so this lands as a
        # "root"/untimed event, same as log_user_input's turn=-1.
        self._logger.log_confirmation(-1, request.reason, decision)
        return decision

    async def ask_open_question(self, prompt: str) -> str:
        # Deliberately NOT logged (no log_* call here at all) — this is
        # the same channel propose_mcp_server uses to collect env-var
        # *values* (secrets) from a human. Those must never reach
        # logs/session-*.jsonl or the GUI's own event stream. See
        # confirmation/terminal_channel.py's ask_open_question() for the
        # same reasoning.
        request_id = uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._sink.write(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event_type": "open_question_request",
                "request_id": request_id,
                "payload": {"prompt": prompt},
            }
        )
        return await future
