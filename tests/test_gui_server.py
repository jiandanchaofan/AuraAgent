"""Integration tests for gui/server.py — a real FastAPI app (via
TestClient, which runs the real ASGI lifespan) wired to the real
build_app_context() against the real shipped config/agents.json, same
"real wiring, no real API key needed" approach as test_bootstrap.py. The
LLM itself is swapped for a FakeLLMProvider after startup so no network
call happens.

test_confirmation_round_trip_does_not_deadlock is the important one: it
would hang forever (and eventually fail on a test timeout) under a naive
implementation that awaits orchestrator.run() directly inside the
WebSocket receive loop, since that loop is also the only thing that can
ever read the confirmation_response the run is waiting on. See
gui/server.py's module docstring for why "user_message" is dispatched as
a background task instead.

Chat-session persistence note: EVERY session's first exchange triggers a
best-effort auto-naming LLM call (gui/server.py::_maybe_autoname_session),
which consumes one extra FakeLLMProvider-scripted response -- so any test
below whose first user_message is the session's first exchange needs
_TITLE_RESPONSE appended after that exchange's own scripted response(s),
or FakeLLMProvider raises "ran out of scripted responses".
"""
from __future__ import annotations

import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from core.message_types import LLMResponse, ToolCallRequest
from gui.server import create_app
from tests.fakes import FakeLLMProvider
from tools.notes.thino_format import insert_thino_line as _insert_thino_line
from tools.notes.thino_format import parse_captured_at as _parse_captured_at
from tools.sessions.session_store import ChatSessionStore

_TITLE_RESPONSE = LLMResponse(thought_text="Auto Title", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])


def _test_settings(tmp_path) -> Settings:
    # A real *copy* of the shipped config/agents.json, not the file itself:
    # propose_capability_grant's approval path (exercised below) persists
    # the grant back to disk, and that must never land in the project's
    # actual config/agents.json.
    agents_config_path = tmp_path / "agents.json"
    shutil.copy(PROJECT_ROOT / "config" / "agents.json", agents_config_path)

    return Settings(
        ANTHROPIC_API_KEY="test-key",
        AURA_NOTES_SANDBOX_ROOT=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        projects_dir=tmp_path / "projects",
        project_meta_dir=tmp_path / "project_meta",
        chat_sessions_dir=tmp_path / "chat_sessions",
        agents_config_path=agents_config_path,
        devices_file=tmp_path / "devices" / "registry.json",
        personal_graph_db_file=tmp_path / "personal_graph" / "graph.db",
        _env_file=None,
    )


def _collect_until(ws, event_type: str, limit: int = 30) -> tuple[list[dict], dict]:
    """Reads events off `ws` until one with the given event_type shows up
    (returned separately), or `limit` messages pass without it (fails)."""
    seen = []
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("event_type") == event_type:
            return seen, event
        seen.append(event)
    raise AssertionError(f"Did not see event_type={event_type!r} within {limit} messages; saw {seen}")


def test_connecting_resumes_or_creates_a_session_first(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["event_type"] == "session_loaded"
            assert first["payload"]["events"] == []
            assert first["payload"]["title"] == "New chat"
            assert "id" in first["payload"]


def test_simple_chat_round_trip_produces_the_expected_event_sequence(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [
                    LLMResponse(thought_text="Hello!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                    _TITLE_RESPONSE,
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded, on connect
            ws.send_json({"type": "user_message", "text": "hi"})

            seen, final = _collect_until(ws, "final_answer")
            event_types = [e["event_type"] for e in seen]
            assert event_types == ["user_input", "calling_llm", "thought"]
            assert final["payload"]["text"] == "Hello!"


def test_confirmation_round_trip_does_not_deadlock(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        # market_new_products is real and installed (a shipped Skill) but
        # NOT in orchestrator's own capabilities (only researcher has it)
        # -- the same safe, real scenario used for live CLI verification
        # of propose_capability_grant during Epic K.
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [
                    LLMResponse(
                        thought_text=None,
                        tool_calls=[
                            ToolCallRequest(
                                "c1", "propose_capability_grant",
                                {"tool_name": "market_new_products", "reason": "test"},
                            )
                        ],
                        stop_reason="tool_use",
                        raw_provider_message=[],
                    ),
                    LLMResponse(thought_text="granted", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                    _TITLE_RESPONSE,
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "user_message", "text": "grant me market_new_products"})

            _, request_event = _collect_until(ws, "confirmation_request")
            assert request_event["payload"]["tool_name"] == "propose_capability_grant"
            assert request_event["payload"]["arguments"] == {"tool_name": "market_new_products"}
            assert "request_id" in request_event

            # If the server awaited orchestrator.run() directly in its
            # receive loop instead of backgrounding it, this send would
            # never be read and the next receive_json() would hang.
            ws.send_json({"type": "confirmation_response", "request_id": request_event["request_id"], "approved": True})

            seen, final = _collect_until(ws, "final_answer")
            assert "confirmation" in [e["event_type"] for e in seen]
            assert final["payload"]["text"] == "granted"

        # Persisted for real, same as the live CLI verification.
        assert client.app.state.ctx.leader_view.is_allowed("market_new_products")


def test_two_messages_on_one_connection_share_conversation_memory(tmp_path):
    # Epic N1: gui/server.py keeps one persistent `history` per connection
    # and passes it into every ctx.orchestrator.run() call on it.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(thought_text="Nice to meet you!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
                LLMResponse(thought_text="Your name is Xiaoming.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "user_message", "text": "My name is Xiaoming."})
            _collect_until(ws, "final_answer")
            _collect_until(ws, "session_renamed")  # the auto-naming push for this first exchange

            ws.send_json({"type": "user_message", "text": "What's my name?"})
            _collect_until(ws, "final_answer")

        # Index 2, not 1 -- index 1 is the auto-naming call's own
        # (differently-shaped, synthesized) history, not a real turn.
        second_call_history = fake_provider.sent_histories[2]
        assert second_call_history[0].text == "My name is Xiaoming."
        assert second_call_history[-1].text == "What's my name?"


def test_a_second_message_while_one_is_in_flight_is_rejected_not_raced(tmp_path):
    # Epic N1's new concurrency guard: with a shared, mutable per-connection
    # `history`, two concurrently-running orchestrator.run() calls on the
    # SAME connection would race appending to it. A confirmation_request
    # is a real, live way to hold the first run open while trying to sneak
    # a second message in.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(
                    thought_text=None,
                    tool_calls=[
                        ToolCallRequest("c1", "propose_capability_grant", {"tool_name": "market_new_products", "reason": "t"})
                    ],
                    stop_reason="tool_use",
                    raw_provider_message=[],
                ),
                LLMResponse(thought_text="granted", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "user_message", "text": "grant me market_new_products"})
            _, request_event = _collect_until(ws, "confirmation_request")

            # The first run is now blocked awaiting this confirmation --
            # try to sneak a second message in while it's still open.
            ws.send_json({"type": "user_message", "text": "sneaky second message"})
            seen, error_event = _collect_until(ws, "error")
            assert "Ignored" in error_event["payload"]["message"]

            ws.send_json({"type": "confirmation_response", "request_id": request_event["request_id"], "approved": True})
            _, final = _collect_until(ws, "final_answer")
            assert final["payload"]["text"] == "granted"

        # Exactly the one run's two turns plus its auto-naming call -- the
        # rejected message never triggered a second orchestrator.run().
        assert fake_provider.sent_histories[0][0].text == "grant me market_new_products"
        assert len(fake_provider.sent_histories) == 3


def test_new_chat_clears_server_side_history(tmp_path):
    # Sidebar "New chat": a brand-new saved session, not just a cleared
    # display -- otherwise the Leader would keep silently remembering
    # earlier turns.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(thought_text="Nice to meet you!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
                LLMResponse(thought_text="I don't know your name.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,  # the second session's OWN first exchange, also auto-named
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            first_session = ws.receive_json()["payload"]["id"]
            ws.send_json({"type": "user_message", "text": "My name is Xiaoming."})
            _collect_until(ws, "final_answer")
            _collect_until(ws, "session_renamed")

            ws.send_json({"type": "new_chat"})
            loaded = _collect_until(ws, "session_loaded")[1]
            assert loaded["payload"]["events"] == []
            assert loaded["payload"]["id"] != first_session

            ws.send_json({"type": "user_message", "text": "What's my name?"})
            _collect_until(ws, "final_answer")

        # If history hadn't been cleared, this would start with the earlier
        # "My name is Xiaoming." turn, same as the un-cleared case tested by
        # test_two_messages_on_one_connection_share_conversation_memory.
        second_session_history = fake_provider.sent_histories[2]
        assert second_session_history[0].text == "What's my name?"


def test_new_chat_while_busy_is_ignored_not_racing_history(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(
                    thought_text=None,
                    tool_calls=[
                        ToolCallRequest("c1", "propose_capability_grant", {"tool_name": "market_new_products", "reason": "t"})
                    ],
                    stop_reason="tool_use",
                    raw_provider_message=[],
                ),
                LLMResponse(thought_text="granted", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "user_message", "text": "grant me market_new_products"})
            _, request_event = _collect_until(ws, "confirmation_request")

            # The run is blocked awaiting this confirmation -- a "new_chat"
            # arriving now must not clear `history` out from under it.
            ws.send_json({"type": "new_chat"})
            seen, error_event = _collect_until(ws, "error")
            assert "Ignored" in error_event["payload"]["message"]

            ws.send_json({"type": "confirmation_response", "request_id": request_event["request_id"], "approved": True})
            _, final = _collect_until(ws, "final_answer")
            assert final["payload"]["text"] == "granted"

        # The original turn is still there -- proof the ignored new_chat
        # never cleared it mid-flight.
        assert fake_provider.sent_histories[0][0].text == "grant me market_new_products"


def test_confirmation_declined_flows_through_too(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [
                    LLMResponse(
                        thought_text=None,
                        tool_calls=[
                            ToolCallRequest(
                                "c1", "propose_capability_grant",
                                {"tool_name": "market_tech_trends", "reason": "test"},
                            )
                        ],
                        stop_reason="tool_use",
                        raw_provider_message=[],
                    ),
                    LLMResponse(thought_text="declined, ok", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                    _TITLE_RESPONSE,
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "user_message", "text": "grant it"})
            _, request_event = _collect_until(ws, "confirmation_request")
            ws.send_json({"type": "confirmation_response", "request_id": request_event["request_id"], "approved": False})
            _, final = _collect_until(ws, "final_answer")
            assert final["payload"]["text"] == "declined, ok"

        assert not client.app.state.ctx.leader_view.is_allowed("market_tech_trends")


def test_first_exchange_auto_names_the_session(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [
                    LLMResponse(thought_text="Paris is the capital.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                    LLMResponse(thought_text="France's Capital City", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            session_id = ws.receive_json()["payload"]["id"]
            ws.send_json({"type": "user_message", "text": "What's the capital of France?"})
            _collect_until(ws, "final_answer")
            _, renamed = _collect_until(ws, "session_renamed")
            assert renamed["payload"] == {"id": session_id, "title": "France's Capital City"}

        # Persisted, not just pushed over the wire.
        store = ChatSessionStore(tmp_path / "chat_sessions")
        info = asyncio.run(store.get_session(session_id))
        assert info.title == "France's Capital City"


def test_a_manual_rename_before_first_message_is_not_overwritten(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [LLMResponse(thought_text="Sure!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            session_id = ws.receive_json()["payload"]["id"]

            resp = client.patch(f"/api/sessions/{session_id}", json={"title": "My own title"})
            assert resp.status_code == 200

            ws.send_json({"type": "user_message", "text": "hi"})
            _collect_until(ws, "final_answer")
            # No session_renamed push -- the FakeLLMProvider only had ONE
            # scripted response; a second (title) call would have raised.

        store = ChatSessionStore(tmp_path / "chat_sessions")
        info = asyncio.run(store.get_session(session_id))
        assert info.title == "My own title"


def test_set_active_session_restores_history_and_resumes_it(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(thought_text="Nice to meet you!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
                LLMResponse(thought_text="Sure, a new one.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
                LLMResponse(thought_text="Your name is Xiaoming.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            first_session = ws.receive_json()["payload"]["id"]
            ws.send_json({"type": "user_message", "text": "My name is Xiaoming."})
            _collect_until(ws, "final_answer")
            _collect_until(ws, "session_renamed")

            ws.send_json({"type": "new_chat"})
            second_session = _collect_until(ws, "session_loaded")[1]["payload"]["id"]
            ws.send_json({"type": "user_message", "text": "unrelated request"})
            _collect_until(ws, "final_answer")
            _collect_until(ws, "session_renamed")

            ws.send_json({"type": "set_active_session", "session_id": first_session})
            loaded = _collect_until(ws, "session_loaded")[1]
            assert loaded["payload"]["id"] == first_session
            assert [e["event_type"] for e in loaded["payload"]["events"]] == [
                "user_input", "calling_llm", "thought", "final_answer"
            ]

            ws.send_json({"type": "user_message", "text": "What's my name?"})
            _collect_until(ws, "final_answer")

        # The resumed session's history starts from ITS OWN first exchange,
        # not the second session's -- proof set_active_session correctly
        # rebuilt `history` from the target session, not the live one.
        resumed_call_history = fake_provider.sent_histories[4]
        assert resumed_call_history[0].text == "My name is Xiaoming."
        assert "unrelated request" not in [t.text for t in resumed_call_history]


def test_set_active_session_unknown_id_reports_error(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "set_active_session", "session_id": "nonexistent"})
            _, error_event = _collect_until(ws, "error")
            assert "No such chat session" in error_event["payload"]["message"]


def test_reconnect_after_restart_resumes_the_most_recent_session(tmp_path):
    settings = _test_settings(tmp_path)

    app1 = create_app(settings)
    with TestClient(app1) as client1:
        client1.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [
                    LLMResponse(thought_text="Hi there!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                    _TITLE_RESPONSE,
                ]
            ),
            "anthropic",
        )
        with client1.websocket_connect("/ws") as ws:
            session_id = ws.receive_json()["payload"]["id"]
            ws.send_json({"type": "user_message", "text": "hello"})
            _collect_until(ws, "final_answer")

    # A fresh app/process against the SAME on-disk sandbox -- simulates
    # stopping and restarting `uvicorn gui.server:app`.
    app2 = create_app(_test_settings(tmp_path))
    with TestClient(app2) as client2:
        with client2.websocket_connect("/ws") as ws:
            resumed = ws.receive_json()
            assert resumed["event_type"] == "session_loaded"
            assert resumed["payload"]["id"] == session_id
            event_types = [e["event_type"] for e in resumed["payload"]["events"]]
            assert "user_input" in event_types
            assert "final_answer" in event_types


# --- Multiple connections (N14) --------------------------------------------


def test_two_connections_do_not_cross_wire_events_or_sessions(tmp_path):
    # Real bug this guards against (N14): WebSocketSink used to be ONE
    # shared outgoing queue and SessionSink ONE shared active_session_id
    # attribute for the whole process -- with two connections open at
    # once (e.g. the desktop GUI plus a connected Auralis phone), either
    # one's live events (and even which session's file they got appended
    # to) could be delivered to/written for the WRONG connection. Both
    # connections resume the same most-recently-updated session on
    # connect (today's documented behavior, unchanged) -- B does
    # "new_chat" to get its own, distinct session to prove isolation
    # against.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        fake_provider = FakeLLMProvider(
            [
                LLMResponse(thought_text="Reply A", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
                LLMResponse(thought_text="Reply B", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                _TITLE_RESPONSE,
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            session_a = ws_a.receive_json()["payload"]["id"]
            ws_b.receive_json()  # session_loaded, same session as A, for now
            ws_b.send_json({"type": "new_chat"})
            session_b = _collect_until(ws_b, "session_loaded")[1]["payload"]["id"]
            assert session_b != session_a

            ws_a.send_json({"type": "user_message", "text": "hello from A"})
            _, final_a = _collect_until(ws_a, "final_answer")
            assert final_a["payload"]["text"] == "Reply A"

            ws_b.send_json({"type": "user_message", "text": "hello from B"})
            _, final_b = _collect_until(ws_b, "final_answer")
            assert final_b["payload"]["text"] == "Reply B"

        # Each session's own persisted file got exactly its own exchange --
        # never the other connection's.
        store: ChatSessionStore = client.app.state.session_store
        events_a = store.read_events(session_a)
        events_b = store.read_events(session_b)
        assert [e["payload"]["text"] for e in events_a if e["event_type"] == "user_input"] == ["hello from A"]
        assert [e["payload"]["text"] for e in events_b if e["event_type"] == "user_input"] == ["hello from B"]


def test_schedule_result_reaches_every_connected_device(tmp_path):
    # N14: a finished scheduled task (tools/scheduler/scheduler_loop.py)
    # isn't "whichever connection is live"'s event -- it's broadcast to
    # every connection, desktop GUI and Auralis phone alike.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            ws_a.receive_json()  # session_loaded
            ws_b.receive_json()  # session_loaded

            client.app.state.ctx.logger.log_schedule_result("sched1", "daily insight", "the result")

            _, result_a = _collect_until(ws_a, "schedule_result")
            _, result_b = _collect_until(ws_b, "schedule_result")
            assert result_a["payload"]["summary"] == "the result"
            assert result_b["payload"]["summary"] == "the result"


# --- _parse_captured_at / _insert_thino_line (unit-level) ------------------


def test_parse_captured_at_naive_iso_is_used_as_is():
    from datetime import datetime

    assert _parse_captured_at("2026-10-02T08:30:15") == datetime(2026, 10, 2, 8, 30, 15)


def test_parse_captured_at_missing_falls_back_to_now():
    from datetime import datetime

    before = datetime.now()
    result = _parse_captured_at(None)
    assert abs((result - before).total_seconds()) < 5


def test_parse_captured_at_malformed_falls_back_to_now():
    from datetime import datetime

    before = datetime.now()
    result = _parse_captured_at("not-a-date")
    assert abs((result - before).total_seconds()) < 5


def test_insert_thino_line_creates_file_with_heading_when_missing(tmp_path):
    path = tmp_path / "2026-10-02 日记.md"

    _insert_thino_line(path, "- 08:00:00 first")

    assert path.read_text(encoding="utf-8") == "## Today's Thino\n\n- 08:00:00 first\n"


def test_insert_thino_line_appends_after_last_bullet(tmp_path):
    path = tmp_path / "2026-10-02 日记.md"
    path.write_text("## Today's Thino\n\n- 08:00:00 first\n", encoding="utf-8")

    _insert_thino_line(path, "- 09:00:00 second")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["## Today's Thino", "", "- 08:00:00 first", "- 09:00:00 second"]


def test_insert_thino_line_adds_heading_when_file_exists_without_it(tmp_path):
    path = tmp_path / "2026-10-02 日记.md"
    path.write_text("# 2026-10-02\n\nexisting text\n", encoding="utf-8")

    _insert_thino_line(path, "- 08:00:00 first")

    content = path.read_text(encoding="utf-8")
    assert content.startswith("# 2026-10-02\n\nexisting text\n\n## Today's Thino\n\n- 08:00:00 first\n")


def test_quick_note_sync_writes_a_thino_style_bullet_into_the_daily_note(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [{"client_id": "c1", "text": "bought milk #Errands", "captured_at": "2026-10-02T08:30:15"}],
                }
            )
            _, ack = _collect_until(ws, "quick_note_sync_ack")
            assert ack["payload"]["accepted_client_ids"] == ["c1"]

        daily_note = client.app.state.ctx.cli_context.notes_root.current / "Daily Notes" / "2026-10-02 日记.md"
        content = daily_note.read_text(encoding="utf-8")
        assert "## Today's Thino" in content
        # Tag position/presence is passed through verbatim, never parsed or moved.
        assert "- 08:30:15 bought milk #Errands" in content


def test_quick_note_sync_routes_items_to_their_own_days(tmp_path):
    # A phone syncing an offline backlog may span several days -- each
    # item must land in ITS OWN day's file, not all in "today"'s.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [
                        {"client_id": "c1", "text": "day one note", "captured_at": "2026-10-01T09:00:00"},
                        {"client_id": "c2", "text": "day two note", "captured_at": "2026-10-02T09:00:00"},
                    ],
                }
            )
            _collect_until(ws, "quick_note_sync_ack")

        notes_root = client.app.state.ctx.cli_context.notes_root.current
        day_one = (notes_root / "Daily Notes" / "2026-10-01 日记.md").read_text(encoding="utf-8")
        day_two = (notes_root / "Daily Notes" / "2026-10-02 日记.md").read_text(encoding="utf-8")
        assert "day one note" in day_one
        assert "day two note" not in day_one
        assert "day two note" in day_two
        assert "day one note" not in day_two


def test_quick_note_sync_inserts_after_existing_bullets_not_at_end_of_file(tmp_path):
    # Real bug this guards against: blindly appending to the end of the
    # whole file would put a new Thino entry after OTHER daily-note
    # content that lives below the Thino section, not inside it.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        daily_dir = client.app.state.ctx.cli_context.notes_root.current / "Daily Notes"
        daily_dir.mkdir(parents=True, exist_ok=True)
        (daily_dir / "2026-10-02 日记.md").write_text(
            "## Today's Thino\n\n- 11:41:45 #Family/Enoch first entry\n\n## Other journal content\n\nunrelated stuff\n",
            encoding="utf-8",
        )

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [{"client_id": "c1", "text": "second entry", "captured_at": "2026-10-02T12:12:12"}],
                }
            )
            _collect_until(ws, "quick_note_sync_ack")

        content = (daily_dir / "2026-10-02 日记.md").read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines.index("- 12:12:12 second entry") == lines.index("- 11:41:45 #Family/Enoch first entry") + 1
        # The unrelated section below must still be below everything Thino.
        assert lines.index("## Other journal content") > lines.index("- 12:12:12 second entry")


def test_quick_note_sync_adds_the_heading_if_the_days_file_exists_without_it(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        daily_dir = client.app.state.ctx.cli_context.notes_root.current / "Daily Notes"
        daily_dir.mkdir(parents=True, exist_ok=True)
        (daily_dir / "2026-10-02 日记.md").write_text("# 2026-10-02\n\nsome existing journal text\n", encoding="utf-8")

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [{"client_id": "c1", "text": "new entry", "captured_at": "2026-10-02T12:00:00"}],
                }
            )
            _collect_until(ws, "quick_note_sync_ack")

        content = (daily_dir / "2026-10-02 日记.md").read_text(encoding="utf-8")
        assert "some existing journal text" in content
        assert "## Today's Thino" in content
        assert "- 12:00:00 new entry" in content


def test_quick_note_sync_follows_the_configurable_quick_notes_subdir(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/notes/quick-notes-dir", json={"subdir": "Journal"})

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [{"client_id": "c1", "text": "note", "captured_at": "2026-10-02T12:00:00"}],
                }
            )
            _collect_until(ws, "quick_note_sync_ack")

        notes_root = client.app.state.ctx.cli_context.notes_root.current
        assert (notes_root / "Journal" / "2026-10-02 日记.md").is_file()
        assert not (notes_root / "Daily Notes" / "2026-10-02 日记.md").exists()


def test_quick_note_sync_skips_malformed_items(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json(
                {
                    "type": "quick_note_sync",
                    "items": [{"client_id": "c1"}, {"text": "no client id"}],  # both missing a required field
                }
            )
            _, ack = _collect_until(ws, "quick_note_sync_ack")
            assert ack["payload"]["accepted_client_ids"] == []


# --- "hello" / sync_catchup (N14 -- a reconnecting Auralis phone) -----------


def test_hello_with_no_last_synced_at_reports_all_recorded_runs(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        schedule = asyncio.run(
            client.app.state.ctx.schedule_store.create_schedule(task="daily insight", trigger_type="recurring", cron_expression="0 9 * * *")
        )
        asyncio.run(client.app.state.ctx.schedule_store.record_run(schedule.id, "the result"))

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "hello"})
            _, catchup = _collect_until(ws, "sync_catchup")

            assert len(catchup["payload"]["schedule_results"]) == 1
            assert catchup["payload"]["schedule_results"][0]["summary"] == "the result"
            assert catchup["payload"]["active_project"] is None


def test_hello_with_last_synced_at_excludes_older_runs(tmp_path):
    from datetime import datetime, timedelta

    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        schedule = asyncio.run(
            client.app.state.ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
        )
        old_run = datetime.now() - timedelta(days=1)
        asyncio.run(client.app.state.ctx.schedule_store.record_run(schedule.id, "yesterday's result", ran_at=old_run))

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "hello", "last_synced_at": datetime.now().isoformat()})
            _, catchup = _collect_until(ws, "sync_catchup")

            assert catchup["payload"]["schedule_results"] == []


def test_hello_reports_the_active_project(tmp_path):
    # Project activation is per-session (Epic N13) -- connecting (or any
    # other chat-switch) re-resolves fresh from the CURRENT session's own
    # project tag, so setting it via a REST /use call before connecting
    # would just get overwritten the moment this connection's own
    # untagged session syncs. Tag the session this connection actually
    # ends up on, on the SAME connection, instead.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # session_loaded
            ws.send_json({"type": "new_chat", "project_slug": "climate"})
            ws.receive_json()  # session_loaded, now tagged to climate

            ws.send_json({"type": "hello"})
            _, catchup = _collect_until(ws, "sync_catchup")

            assert catchup["payload"]["active_project"] == {"slug": "climate", "name": "climate"}


# --- Project <-> Chat sync (gui/server.py::_sync_active_project) -----------


def test_set_active_session_activates_its_tagged_project(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})

        with client.websocket_connect("/ws") as ws:
            session_id = ws.receive_json()["payload"]["id"]
            client.post(f"/api/sessions/{session_id}/project", json={"slug": "climate"})

            # Retagging via REST doesn't sync mid-connection (see
            # gui/routes.py's set_session_project docstring) -- re-activating
            # the SAME session is what triggers the sync.
            ws.send_json({"type": "set_active_session", "session_id": session_id})
            loaded = _collect_until(ws, "session_loaded")[1]
            assert loaded["payload"]["id"] == session_id

        assert client.app.state.ctx.active_project.current_slug == "climate"
        climate_dir = (tmp_path / "projects" / "climate").resolve()
        assert client.app.state.ctx.workspace_root.current == climate_dir


def test_new_chat_inherits_the_currently_active_project(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})

        with client.websocket_connect("/ws") as ws:
            first_session = ws.receive_json()["payload"]["id"]
            client.post(f"/api/sessions/{first_session}/project", json={"slug": "climate"})
            ws.send_json({"type": "set_active_session", "session_id": first_session})
            _collect_until(ws, "session_loaded")
            assert client.app.state.ctx.active_project.current_slug == "climate"

            ws.send_json({"type": "new_chat"})
            new_session = _collect_until(ws, "session_loaded")[1]["payload"]["id"]

        store = client.app.state.session_store
        info = asyncio.run(store.get_session(new_session))
        assert info.project_slug == "climate"
        # Already active -- inheriting it is a no-op sync, not a re-switch.
        assert client.app.state.ctx.active_project.current_slug == "climate"


def test_switching_to_an_untagged_chat_exits_the_active_project(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})
        original_workspace = client.app.state.ctx.workspace_root.current

        with client.websocket_connect("/ws") as ws:
            tagged_session = ws.receive_json()["payload"]["id"]
            client.post(f"/api/sessions/{tagged_session}/project", json={"slug": "climate"})
            ws.send_json({"type": "set_active_session", "session_id": tagged_session})
            _collect_until(ws, "session_loaded")
            assert client.app.state.ctx.active_project.current_slug == "climate"

            # A brand-new chat with no project tag -- but climate is
            # currently active, so it would inherit it (see the test
            # above); create an explicitly-untagged one directly via the
            # store to isolate testing the "switch to untagged" path.
            untagged = asyncio.run(client.app.state.session_store.create_session())
            ws.send_json({"type": "set_active_session", "session_id": untagged.id})
            _collect_until(ws, "session_loaded")

        assert client.app.state.ctx.active_project.current_slug is None
        assert client.app.state.ctx.workspace_root.current == original_workspace


def test_switching_between_two_projects_tagged_chats_resyncs_each_time(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})
        client.post("/api/projects", json={"slug": "writing"})
        store = client.app.state.session_store
        climate_chat = asyncio.run(store.create_session(project_slug="climate"))
        writing_chat = asyncio.run(store.create_session(project_slug="writing"))

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial session_loaded for whichever was most recent

            ws.send_json({"type": "set_active_session", "session_id": climate_chat.id})
            _collect_until(ws, "session_loaded")
            assert client.app.state.ctx.active_project.current_slug == "climate"

            ws.send_json({"type": "set_active_session", "session_id": writing_chat.id})
            _collect_until(ws, "session_loaded")
            assert client.app.state.ctx.active_project.current_slug == "writing"


def test_search_project_chats_tool_is_registered_and_allowed(tmp_path):
    """Phase 4 wiring check: search_project_chats needs a ChatSessionStore
    (gui/server.py-only, see tools/projects/project_tool.py's module
    docstring) -- confirms it actually got registered into the real app's
    shared registry and is visible to the Leader's own view, not just
    listed in config/agents.json's capabilities with nothing behind it."""
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        assert "search_project_chats" in {s.name for s in ctx.registry.get_tool_specs()}
        assert ctx.leader_view.is_allowed("search_project_chats")


def test_search_project_chats_end_to_end_finds_a_match_in_another_chat(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/api/projects", json={"slug": "climate"})
        store = client.app.state.session_store

        other_chat = asyncio.run(store.create_session(project_slug="climate"))
        store.append_event_sync(
            other_chat.id,
            {"agent_name": "orchestrator", "event_type": "user_input", "payload": {"text": "the deadline is June 1st"}},
        )

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial session_loaded
            ws.send_json({"type": "new_chat", "project_slug": "climate"})
            _collect_until(ws, "session_loaded")

            client.app.state.ctx.provider.set_current(
                FakeLLMProvider(
                    [
                        LLMResponse(
                            thought_text="Let me check other chats",
                            tool_calls=[
                                ToolCallRequest(call_id="1", tool_name="search_project_chats", arguments={"query": "deadline"})
                            ],
                            stop_reason="tool_use",
                            raw_provider_message=[],
                        ),
                        LLMResponse(thought_text="Found it", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
                        _TITLE_RESPONSE,
                    ]
                ),
                "anthropic",
            )
            ws.send_json({"type": "user_message", "text": "did we set a deadline before?"})
            _seen, observation = _collect_until(ws, "observation")
            assert "June 1st" in observation["payload"]["content"]


def test_resuming_a_chat_with_a_deleted_project_reports_error_not_crash(tmp_path):
    # The realistic version of this scenario: the ONLY (so most-recently-
    # updated) saved chat is tagged to a project that no longer exists --
    # reconnecting resumes it automatically (module docstring), which is
    # exactly when the very first _sync_active_project call has to cope
    # with a stale reference, before the receive loop even starts.
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        store = client.app.state.session_store
        stale_chat = asyncio.run(store.create_session(project_slug="ghost_project"))

        with client.websocket_connect("/ws") as ws:
            seen, loaded = _collect_until(ws, "session_loaded")
            assert any(e["event_type"] == "error" for e in seen)
            assert loaded["payload"]["id"] == stale_chat.id  # the chat itself still resumes fine

        assert client.app.state.ctx.active_project.current_slug is None
