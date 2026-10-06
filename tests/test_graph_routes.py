"""Tests for gui/graph_routes.py -- the human-facing REST CRUD surface over
the personal data graph (footprints/persons/projects/trash) plus the async
AI-enrichment task. CRUD endpoints are thin wrappers over GraphStore (already
covered by tests/test_graph_store.py) so get 1-2 tests each; the
AI-enrichment path and the "editing never retriggers AI" guarantee are
higher-risk and get more coverage.

Every test swaps in a FakeLLMProvider right after entering TestClient,
before any POST /api/graph/footprints -- that route always schedules a REAL
asyncio.create_task(_enrich_footprint(...)) in the background (Option A,
fire-and-forget), so leaving the real (fake-keyed) AnthropicProvider in
place would let that background task attempt a real network call on every
single test. The AI-enrichment-specific tests exercise that SAME real
background task (never `_enrich_footprint` called directly) and poll the
GET endpoint briefly for it to land -- `ctx.graph_store` holds a raw
sqlite3.Connection bound to the thread that created it (TestClient's own
portal thread, via FastAPI's lifespan startup); calling it via a second,
separate `asyncio.run()` from the test's own thread while that real
background task is concurrently contending for the store's asyncio.Lock
from the PORTAL thread's loop is a genuine cross-thread/cross-loop deadlock
(reproduced and confirmed while writing this file) -- every call that
touches ctx.graph_store must go through the real HTTP routes instead, which
always run on the portal thread.
"""
from __future__ import annotations

import json
import shutil
import time

from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from core.message_types import LLMResponse
from gui.server import create_app
from tests.fakes import FakeLLMProvider


def _test_settings(tmp_path) -> Settings:
    agents_config_path = tmp_path / "agents.json"
    shutil.copy(PROJECT_ROOT / "config" / "agents.json", agents_config_path)
    # No MCP servers for these tests -- the real config/mcp_servers.json spawns
    # an `npx` filesystem server per create_app() call, which is slow and adds
    # nothing here (none of these tests touch MCP).
    mcp_config_path = tmp_path / "mcp_servers.json"
    mcp_config_path.write_text(json.dumps({"servers": []}), encoding="utf-8")
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
        mcp_config_path=mcp_config_path,
        devices_file=tmp_path / "devices" / "registry.json",
        personal_graph_db_file=tmp_path / "personal_graph" / "graph.db",
        _env_file=None,
    )


def _use_fake_provider(client: TestClient, responses: list[LLMResponse]) -> None:
    client.app.state.ctx.provider.set_current(FakeLLMProvider(responses), "anthropic")


def _poll_until(predicate, *, timeout_s: float = 2.0, interval_s: float = 0.05):
    """Polls `predicate()` (a zero-arg callable returning a truthy result to
    stop on) until it's truthy or `timeout_s` elapses, returning the last
    result either way. The real AI-enrichment task is scheduled via
    asyncio.create_task right after the HTTP response is sent -- with a
    FakeLLMProvider (instant, no real I/O) it normally lands within a
    millisecond or two, but this avoids any timing assumption."""
    deadline = time.monotonic() + timeout_s
    result = predicate()
    while not result and time.monotonic() < deadline:
        time.sleep(interval_s)
        result = predicate()
    return result


# --- Footprints ----------------------------------------------------------


def test_post_footprint_applies_heuristic_classification_and_links(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])
        resp = client.post("/api/graph/footprints", json={"text": "worked on it", "project_tag": "fitness"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "project"
        assert body["type_source"] == "heuristic"
        assert body["links"]["projects"][0]["tag"] == "fitness"


def test_patch_footprint_edits_text_without_touching_type_or_scheduling_ai(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])
        created = client.post("/api/graph/footprints", json={"text": "original", "project_tag": "fitness"}).json()

        resp = client.patch(f"/api/graph/footprints/{created['id']}", json={"text": "edited"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "edited"
        assert body["type"] == "project"  # untouched
        assert body["type_source"] == "heuristic"  # untouched -- PATCH never sets type_source


def test_delete_then_restore_footprint_round_trip_via_rest(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])
        created = client.post("/api/graph/footprints", json={"text": "temp"}).json()

        client.delete(f"/api/graph/footprints/{created['id']}")
        assert client.get("/api/graph/footprints").json() == []

        restore_resp = client.post(f"/api/graph/trash/footprint/{created['id']}/restore")
        assert restore_resp.json()["status"] == "ok"
        assert len(client.get("/api/graph/footprints").json()) == 1


def test_get_footprints_includes_link_chips_with_source(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])
        created = client.post("/api/graph/footprints", json={"text": "x", "person_names": ["Xiaoming"]}).json()

        resp = client.get("/api/graph/footprints")

        body = resp.json()
        assert body[0]["id"] == created["id"]
        assert body[0]["links"]["persons"][0]["name"] == "Xiaoming"
        assert body[0]["links"]["persons"][0]["link_source"] == "user"


def test_dismiss_link_sets_dismissed_flag(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])
        created = client.post("/api/graph/footprints", json={"text": "x", "person_names": ["Xiaoming"]}).json()
        link_id = created["links"]["persons"][0]["link_id"]

        resp = client.post(f"/api/graph/links/{link_id}/dismiss")

        assert resp.json()["status"] == "ok"
        refetched = client.get("/api/graph/footprints").json()
        assert refetched[0]["links"]["persons"] == []


# --- Persons / Projects CRUD -----------------------------------------------


def test_persons_crud_round_trip(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/graph/persons", json={"name": "Xiaoming"}).json()
        assert created["name"] == "Xiaoming"

        updated = client.patch(f"/api/graph/persons/{created['id']}", json={"key_info": "colleague"}).json()
        assert updated["key_info"] == "colleague"

        client.delete(f"/api/graph/persons/{created['id']}")
        assert client.get("/api/graph/persons").json() == []


def test_projects_crud_round_trip(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/graph/projects", json={"tag": "fitness", "name": "Get fit"}).json()
        assert created["tag"] == "fitness"

        updated = client.patch(f"/api/graph/projects/{created['id']}", json={"goal": "run 5k"}).json()
        assert updated["goal"] == "run 5k"

        client.delete(f"/api/graph/projects/{created['id']}")
        assert client.get("/api/graph/projects").json() == []


def test_trash_list_and_restore_via_rest(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/graph/persons", json={"name": "Xiaoming"}).json()
        client.delete(f"/api/graph/persons/{created['id']}")

        trashed = client.get("/api/graph/trash", params={"entity": "person"}).json()
        assert [p["id"] for p in trashed] == [created["id"]]

        client.post(f"/api/graph/trash/person/{created['id']}/restore")
        assert client.get("/api/graph/persons").json()[0]["id"] == created["id"]


def test_graph_routes_require_device_token_when_auth_enabled(tmp_path):
    settings = _test_settings(tmp_path)
    settings = settings.model_copy(update={"require_auth": True})
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/api/graph/footprints")
        assert resp.status_code == 401


# --- Async AI-enrichment (exercised through the real fire-and-forget route,
# never by calling _enrich_footprint directly -- see module docstring) -----


def test_enrich_footprint_writes_ai_type_and_links_on_valid_json(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(
            client,
            [LLMResponse(thought_text='{"type": "interpersonal", "person_names": ["Xiaoming"], "project_tag": null}', tool_calls=[], stop_reason="end_turn")],
        )
        client.post("/api/graph/footprints", json={"text": "ran with Xiaoming"})

        footprint = _poll_until(lambda: next((f for f in client.get("/api/graph/footprints").json() if f["type_source"] == "ai"), None))

        assert footprint is not None
        assert footprint["type"] == "interpersonal"
        assert footprint["links"]["persons"][0]["name"] == "Xiaoming"
        assert footprint["links"]["persons"][0]["link_source"] == "ai"


def test_enrich_footprint_is_a_noop_on_malformed_json(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [LLMResponse(thought_text="not json at all", tool_calls=[], stop_reason="end_turn")])
        client.post("/api/graph/footprints", json={"text": "hmm"})

        # Nothing to poll FOR (a no-op produces no observable change) -- give
        # the background task a moment to run, then assert it changed nothing.
        time.sleep(0.3)
        footprint = client.get("/api/graph/footprints").json()[0]
        assert footprint["type_source"] == "default"


def test_enrich_footprint_never_raises_when_provider_errors(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        _use_fake_provider(client, [])  # FakeLLMProvider raises "ran out of scripted responses" on send()
        resp = client.post("/api/graph/footprints", json={"text": "hmm"})
        assert resp.status_code == 200

        time.sleep(0.3)
        # The connection/app must still be healthy -- a background task's
        # exception must never propagate anywhere that could break this.
        assert client.get("/api/graph/footprints").json()[0]["type_source"] == "default"
