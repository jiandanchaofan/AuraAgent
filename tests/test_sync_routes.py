"""Tests for gui/sync_routes.py -- the Auralis personal-data-graph REST
sync surface: push/changes round trip, the since= cursor, device-token
auth gating, and the sync_signal broadcast.
"""
from __future__ import annotations

import asyncio
import shutil

from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from gui.server import create_app


def _test_settings(tmp_path, require_auth: bool = False) -> Settings:
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
        AURA_REQUIRE_AUTH=require_auth,
        _env_file=None,
    )


def test_push_creates_a_footprint_and_changes_reports_it(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post(
            "/api/sync/push",
            json={"ops": [{"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "hello"}}]},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == [{"op_id": "op1", "status": "ok", "version": 0}]

        resp = client.get("/api/sync/changes")
        body = resp.json()
        assert body["latest_seq"] == 1
        assert body["changes"][0]["entity_id"] == "fp1"


def test_changes_since_excludes_earlier_seqs(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        client.post(
            "/api/sync/push",
            json={"ops": [{"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}}]},
        )
        client.post(
            "/api/sync/push",
            json={"ops": [{"op_id": "op2", "entity": "footprint", "entity_id": "fp2", "op": "upsert", "patch": {"text": "b"}}]},
        )

        resp = client.get("/api/sync/changes", params={"since": 1})

        body = resp.json()
        assert [c["entity_id"] for c in body["changes"]] == ["fp2"]
        assert body["latest_seq"] == 2


def test_push_is_idempotent_via_op_id(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        op = {"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}}
        client.post("/api/sync/push", json={"ops": [op]})

        resp = client.post("/api/sync/push", json={"ops": [op]})

        assert resp.json()["results"] == [{"op_id": "op1", "status": "duplicate", "version": None}]


def test_push_batches_multiple_ops(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post(
            "/api/sync/push",
            json={
                "ops": [
                    {"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}},
                    {"op_id": "op2", "entity": "person", "entity_id": "p1", "op": "upsert", "patch": {"name": "X"}},
                ]
            },
        )

        results = resp.json()["results"]
        assert [r["op_id"] for r in results] == ["op1", "op2"]
        assert all(r["status"] == "ok" for r in results)


def test_push_without_device_token_is_rejected_when_auth_required(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        resp = client.post(
            "/api/sync/push",
            json={"ops": [{"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}}]},
        )
        assert resp.status_code == 401


def test_push_with_valid_device_token_is_accepted_when_auth_required(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        _, raw_token = asyncio.run(client.app.state.ctx.device_store.create_device("test-phone"))

        resp = client.post(
            "/api/sync/push",
            headers={"Authorization": f"Bearer {raw_token}"},
            json={"ops": [{"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}}]},
        )

        assert resp.status_code == 200


def test_push_broadcasts_a_sync_signal_to_every_connection(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            ws_a.receive_json()  # session_loaded
            ws_b.receive_json()  # session_loaded

            client.post(
                "/api/sync/push",
                json={"ops": [{"op_id": "op1", "entity": "footprint", "entity_id": "fp1", "op": "upsert", "patch": {"text": "a"}}]},
            )

            signal_a = ws_a.receive_json()
            signal_b = ws_b.receive_json()
            assert signal_a == {"event_type": "sync_signal", "payload": {"latest_seq": 1}, "ts": signal_a["ts"]}
            assert signal_b["payload"]["latest_seq"] == 1
