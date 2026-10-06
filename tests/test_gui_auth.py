"""Tests for gui/auth.py's enforcement as wired into gui/server.py (N14):
settings.require_auth off (the default) must leave every existing REST/WS
behavior untouched; turned on, both must require a valid device token.
"""
from __future__ import annotations

import asyncio
import shutil

from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from gui.server import create_app


def _test_settings(tmp_path, require_auth: bool) -> Settings:
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


def test_require_auth_off_by_default_rest_is_unaffected(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=False))
    with TestClient(app) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200


def test_require_auth_off_by_default_ws_is_unaffected(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=False))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["event_type"] == "session_loaded"


def test_require_auth_on_rejects_rest_without_token(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 401


def test_require_auth_on_rejects_rest_with_bad_token(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        resp = client.get("/api/config", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401


def test_require_auth_on_accepts_rest_with_valid_token(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        _, raw_token = asyncio.run(client.app.state.ctx.device_store.create_device("test-phone"))
        resp = client.get("/api/config", headers={"Authorization": f"Bearer {raw_token}"})
        assert resp.status_code == 200


def test_require_auth_on_rejects_ws_without_token(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws"):
                raise AssertionError("expected the handshake to be rejected")
        except Exception:
            pass  # starlette's test client raises on a rejected handshake


def test_require_auth_on_accepts_ws_with_valid_token(tmp_path):
    app = create_app(_test_settings(tmp_path, require_auth=True))
    with TestClient(app) as client:
        _, raw_token = asyncio.run(client.app.state.ctx.device_store.create_device("test-phone"))
        with client.websocket_connect(f"/ws?token={raw_token}") as ws:
            first = ws.receive_json()
            assert first["event_type"] == "session_loaded"
