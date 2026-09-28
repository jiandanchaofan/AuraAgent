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
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from core.message_types import LLMResponse, ToolCallRequest
from gui.server import create_app
from tests.fakes import FakeLLMProvider


def _test_settings(tmp_path) -> Settings:
    # A real *copy* of the shipped config/agents.json, not the file itself:
    # propose_capability_grant's approval path (exercised below) persists
    # the grant back to disk, and that must never land in the project's
    # actual config/agents.json.
    agents_config_path = tmp_path / "agents.json"
    shutil.copy(PROJECT_ROOT / "config" / "agents.json", agents_config_path)

    return Settings(
        ANTHROPIC_API_KEY="test-key",
        notes_sandbox_root=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        projects_dir=tmp_path / "projects",
        project_meta_dir=tmp_path / "project_meta",
        agents_config_path=agents_config_path,
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


def test_simple_chat_round_trip_produces_the_expected_event_sequence(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.ctx.provider.set_current(
            FakeLLMProvider(
                [LLMResponse(thought_text="Hello!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
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
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
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
                LLMResponse(thought_text="Your name is Xiaoming.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user_message", "text": "My name is Xiaoming."})
            _collect_until(ws, "final_answer")

            ws.send_json({"type": "user_message", "text": "What's my name?"})
            _collect_until(ws, "final_answer")

        second_call_history = fake_provider.sent_histories[1]
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
            ]
        )
        client.app.state.ctx.provider.set_current(fake_provider, "anthropic")

        with client.websocket_connect("/ws") as ws:
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

        # Only ever one LLMResponse pair consumed -- the rejected message
        # never triggered a second orchestrator.run() at all.
        assert fake_provider.sent_histories[0][0].text == "grant me market_new_products"
        assert len(fake_provider.sent_histories) == 2


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
                ]
            ),
            "anthropic",
        )
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user_message", "text": "grant it"})
            _, request_event = _collect_until(ws, "confirmation_request")
            ws.send_json({"type": "confirmation_response", "request_id": request_event["request_id"], "approved": False})
            _, final = _collect_until(ws, "final_answer")
            assert final["payload"]["text"] == "declined, ok"

        assert not client.app.state.ctx.leader_view.is_allowed("market_tech_trends")
