"""Integration tests for gui/routes.py — the REST endpoints backing the
GUI's Settings/Team/Skills panels (Epic M3). Same "real build_app_context()
against a sandboxed copy of the real config" approach as test_gui_server.py
-- these hit the real cli/service.py functions cli/commands.py's "/"
handlers also call, so a passing test here is also a live check that the
REST/", command layers stay behaviorally identical.

env_file_path is sandboxed too (Settings.env_file_path, tmp_path/".env")
-- a real gap this test file's development caught: core/bootstrap.py used
to hardcode PROJECT_ROOT/".env" for CLIContext.env_file_path regardless of
the settings object passed in, so a test exercising /config/set-key would
have silently written into the real project .env. Fixed in
config/settings.py + core/bootstrap.py alongside adding these tests.
"""
from __future__ import annotations

import base64
import shutil

import pytest
from fastapi.testclient import TestClient

from config.settings import PROJECT_ROOT, Settings
from gui.server import create_app

_SKILL_MD = "---\nname: demo_skill\ndescription: a demo\n---\nbody\n"
_RUN_PY = (
    "import argparse\n"
    "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
    "p.parse_args()\n"
    "print('ok')\n"
)


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _test_settings(tmp_path) -> Settings:
    agents_config_path = tmp_path / "agents.json"
    shutil.copy(PROJECT_ROOT / "config" / "agents.json", agents_config_path)

    return Settings(
        ANTHROPIC_API_KEY="test-key",
        OPENAI_API_KEY="",
        notes_sandbox_root=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        agents_config_path=agents_config_path,
        skills_dir=tmp_path / "skills_store",
        env_file_path=tmp_path / ".env",
        _env_file=None,
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(_test_settings(tmp_path))
    with TestClient(app) as c:
        yield c


# --- /api/config -------------------------------------------------------------


def test_get_config_reports_masked_key(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert "test-key" not in body["key_masked"]


def test_post_config_use_unknown_provider_rejected(client):
    resp = client.post("/api/config/use", json={"provider": "bogus", "model": "x"})
    assert resp.status_code == 400
    assert "Unknown provider" in resp.json()["detail"]


def test_post_config_use_without_a_known_key_is_rejected(client):
    resp = client.post("/api/config/use", json={"provider": "openai", "model": "deepseek-chat"})
    assert resp.status_code == 400
    assert "No API key known" in resp.json()["detail"]


def test_post_config_set_key_then_use_switches_provider(client, tmp_path):
    resp = client.post("/api/config/set-key", json={"provider": "openai", "value": "sk-test-0000"})
    assert resp.status_code == 200

    env_file = tmp_path / ".env"
    assert env_file.exists()
    assert "sk-test-0000" in env_file.read_text(encoding="utf-8")

    resp = client.post("/api/config/use", json={"provider": "openai", "model": "deepseek-chat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai"
    assert body["model"] == "deepseek-chat"


def test_post_config_set_key_empty_value_rejected(client):
    resp = client.post("/api/config/set-key", json={"provider": "anthropic", "value": "   "})
    assert resp.status_code == 400
    assert "Empty key" in resp.json()["detail"]


# --- /api/agents ---------------------------------------------------------


def test_get_agents_lists_leader_and_workers(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "orchestrator" in names
    assert "researcher" in names


def test_post_agents_adds_a_working_delegate_tool_and_persists(client, tmp_path):
    resp = client.post(
        "/api/agents",
        json={"name": "analyst", "system_prompt": "You are an analyst.", "capabilities": ["calculate"]},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "analyst"

    names = {a["name"] for a in client.get("/api/agents").json()}
    assert "analyst" in names


def test_post_agents_rejects_name_collision(client):
    resp = client.post(
        "/api/agents", json={"name": "orchestrator", "system_prompt": "x", "capabilities": ["calculate"]}
    )
    assert resp.status_code == 409


def test_post_agents_rejects_invalid_definition(client):
    resp = client.post("/api/agents", json={"name": "not a valid name!", "system_prompt": "x", "capabilities": []})
    assert resp.status_code == 400
    assert "Invalid agent definition" in resp.json()["detail"]


def test_delete_agent_removes_worker(client):
    client.post("/api/agents", json={"name": "analyst", "system_prompt": "x", "capabilities": ["calculate"]})

    resp = client.delete("/api/agents/analyst")
    assert resp.status_code == 200

    names = {a["name"] for a in client.get("/api/agents").json()}
    assert "analyst" not in names


def test_delete_agent_leader_is_rejected(client):
    resp = client.delete("/api/agents/orchestrator")
    assert resp.status_code == 404
    assert "Cannot remove the leader" in resp.json()["detail"]


def test_delete_agent_unknown_name_rejected(client):
    resp = client.delete("/api/agents/nonexistent")
    assert resp.status_code == 404


# --- /api/skills ---------------------------------------------------------


def test_get_skills_starts_empty(client):
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json() == []


def test_stage_then_commit_skill_from_upload(client, tmp_path):
    zip_bytes = _make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY})
    stage_resp = client.post(
        "/api/skills/stage",
        json={"source": "upload", "content_base64": base64.b64encode(zip_bytes).decode("ascii")},
    )
    assert stage_resp.status_code == 200
    staged = stage_resp.json()
    assert staged["name"] == "demo_skill"
    assert "def " not in staged["run_py_text"] or "argparse" in staged["run_py_text"]  # full code, not a summary
    assert "staging_id" in staged

    commit_resp = client.post("/api/skills/commit", json={"staging_id": staged["staging_id"]})
    assert commit_resp.status_code == 200
    assert commit_resp.json()["name"] == "demo_skill"

    names = {s["name"] for s in client.get("/api/skills").json()}
    assert "demo_skill" in names


def test_stage_rejects_malformed_package(client):
    zip_bytes = _make_zip_bytes({"SKILL.md": _SKILL_MD})  # missing run.py
    resp = client.post(
        "/api/skills/stage",
        json={"source": "upload", "content_base64": base64.b64encode(zip_bytes).decode("ascii")},
    )
    assert resp.status_code == 422


def test_stage_from_url_rejects_non_http_scheme(client):
    resp = client.post("/api/skills/stage", json={"source": "url", "url": "file:///etc/passwd"})
    assert resp.status_code == 400


def test_commit_unknown_staging_id_rejected(client):
    resp = client.post("/api/skills/commit", json={"staging_id": "nonexistent"})
    assert resp.status_code == 404
