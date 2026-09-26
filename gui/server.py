"""FastAPI backend for the AuraAgent GUI.

Calls the exact same core/bootstrap.py::build_app_context() the CLI
(main.py) does, swapping in a WebSocketConfirmationChannel + WebSocketSink
instead of TerminalConfirmationChannel + TerminalSink — everything else
(the tool registry, the team, all six self-extension tools) is identical.
This is the architectural guarantee behind "CLI and GUI stay feature-equal"
(see core/bootstrap.py's module docstring).

M1 scope (this module): a single shared AppContext for the whole process's
lifetime — one "session", one orchestrator instance, matching main.py's
own single-REPL-loop model. One /ws endpoint carrying both the outgoing
white-box event stream and the bidirectional confirmation protocol. No
REST endpoints yet (added in M3, alongside the cli/commands.py
print()-decoupling that will let "/" commands' output actually reach a
GUI client instead of only the server process's own stdout — so slash
commands are deliberately NOT wired into the WebSocket protocol yet).

M2 adds the actual frontend (gui/frontend/, a minimal React+Vite app) —
`npm run build` there outputs straight into gui/static/, which this module
mounts and serves at "/" whenever that directory exists, so a single
`uvicorn gui.server:app` serves both the WebSocket API and the built UI.
If gui/static/ hasn't been built yet (e.g. in tests, or before running
`npm run build`), "/" simply isn't mounted and /ws still works fine on its
own — that's how M1 was exercised before any frontend existed, with a raw
WebSocket client/script.

Wire protocol (JSON messages):
  Server -> client: every AuraLogger event (user_input/calling_llm/
    thought/tool_call/observation/confirmation/final_answer/error — see
    core/logger.py), PLUS confirmation_request / open_question_request
    (from ConfirmationChannel, see gui/ws_channel.py) — all share the
    same {event_type, payload, ...} shape on one ordered stream.
  Client -> server: {"type": "user_message", "text": "..."} to run the
    orchestrator; {"type": "confirmation_response", "request_id": ...,
    "approved": true|false}; {"type": "open_question_response",
    "request_id": ..., "answer": "..."}.

IMPORTANT concurrency note: handling "user_message" must NEVER block this
handler's `while True: await websocket.receive_text()` loop. orchestrator
.run() can itself block inside a ConfirmationChannel.confirm() call,
waiting for a "confirmation_response" message — and that message can only
ever arrive by this SAME loop reaching receive_text() again. Running the
orchestrator inline here would deadlock: the run() that's waiting for a
response would itself be what's preventing that response from ever being
read. So "user_message" is dispatched as a background asyncio.Task, never
awaited directly in the receive loop.

Epic N1 gives each connection a persistent `history` list, passed into
every `ctx.orchestrator.run(text, history=history)` call so the Leader
actually remembers earlier turns within one connection (one open chat =
one conversation; a reconnect starts fresh, same as a new `python
main.py` process). Because "user_message" runs as a background Task
rather than being awaited inline, a SECOND "user_message" arriving while
the first is still running would race on that same mutable list — so a
"user_message" is rejected (not queued or dispatched) whenever `run_tasks`
already shows a run in flight; the frontend already disables its input
while busy, this is just the server-side enforcement of the same rule.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from config.settings import Settings, load_settings
from core.bootstrap import AppContext, build_app_context
from core.logger import AuraLogger, JSONLSink
from core.message_types import ConversationTurn
from gui.routes import router as api_router
from gui.ws_channel import WebSocketConfirmationChannel
from gui.ws_log_sink import WebSocketSink

#: Where `npm run build` (gui/frontend/) outputs the production bundle —
#: exposed at module level (not just a local inside create_app()) so
#: gui_app.py (Epic M4's desktop entry point) can check it exists and give
#: a clear message before ever opening a window against a backend with no
#: UI mounted.
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """`settings` is injectable so tests can pass a Settings pointed at
    tmp_path sandboxes (same pattern as tests/test_bootstrap.py) instead
    of requiring a real .env/API key — `uvicorn gui.server:app` (the real
    entry point, see the module-level `app` below) always uses the real
    load_settings()."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings if settings is not None else load_settings()
        sink = WebSocketSink()
        logger = AuraLogger([JSONLSink(resolved_settings.logs_dir), sink])
        confirmation_channel = WebSocketConfirmationChannel(sink, logger)
        app.state.ctx = await build_app_context(resolved_settings, confirmation_channel, logger)
        app.state.sink = sink
        # Skills staged via POST /api/skills/stage but not yet committed
        # (Epic M3) -- in-memory only, keyed by a uuid; same "small,
        # process-lifetime pending state" shape as ws_channel.py's
        # pending-Futures dict, just for a two-step HTTP flow instead of a
        # WebSocket round trip.
        app.state.staged_skills = {}
        try:
            yield
        finally:
            await app.state.ctx.aclose()

    app = FastAPI(title="AuraAgent GUI backend", lifespan=_lifespan)
    app.include_router(api_router)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        ctx: AppContext = websocket.app.state.ctx
        sink: WebSocketSink = websocket.app.state.sink
        confirmation_channel = ctx.confirmation_channel
        assert isinstance(confirmation_channel, WebSocketConfirmationChannel)

        drain_task = asyncio.create_task(sink.drain(websocket))
        # Tracks in-flight orchestrator runs so they can be cancelled if
        # the connection drops, and so asyncio doesn't warn about a task
        # result never being retrieved (see _run_orchestrator's own
        # try/except for the "surface errors instead of losing them" half).
        # Also doubles as the busy flag guarding `history` below: non-empty
        # means a run is in flight on this connection right now.
        run_tasks: set[asyncio.Task[None]] = set()
        # Persistent per-connection conversation memory (Epic N1) — mutated
        # in place by every ctx.orchestrator.run() call below, see
        # AsyncReActEngine.run()'s docstring for why that's safe here but
        # NOT for Worker engines (agents/delegate_tool.py never passes one).
        history: list[ConversationTurn] = []
        try:
            while True:
                raw = await websocket.receive_text()
                message = json.loads(raw)
                msg_type = message.get("type")
                if msg_type == "user_message":
                    if run_tasks:
                        ctx.logger.log_error(
                            -1,
                            "Ignored: a previous message is still being processed on this connection.",
                            agent_name=ctx.agent_registry.leader.name,
                        )
                        continue
                    task = asyncio.create_task(_run_orchestrator(ctx, message["text"], history))
                    run_tasks.add(task)
                    task.add_done_callback(run_tasks.discard)
                elif msg_type == "confirmation_response":
                    confirmation_channel.resolve(message["request_id"], message["approved"])
                elif msg_type == "open_question_response":
                    confirmation_channel.resolve(message["request_id"], message["answer"])
                # An unrecognized message type is silently ignored rather
                # than closing the connection — matches main.py's REPL,
                # which tolerates a blank/malformed line without crashing
                # the whole session over it.
        except WebSocketDisconnect:
            pass
        finally:
            drain_task.cancel()
            for task in run_tasks:
                task.cancel()

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


async def _run_orchestrator(ctx: AppContext, text: str, history: list[ConversationTurn]) -> None:
    try:
        await ctx.orchestrator.run(text, history=history)
    except Exception as exc:  # noqa: BLE001 - surface to the GUI's event stream, mirroring main.py's REPL
        ctx.logger.log_error(-1, f"{type(exc).__name__}: {exc}")


app = create_app()
