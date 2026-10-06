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

import asyncio
import base64
import shutil
from pathlib import Path

import pytest
from dotenv import dotenv_values
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
        AURA_NOTES_SANDBOX_ROOT=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        google_client_secret_file=tmp_path / "calendar" / "google_client_secret.json",
        google_token_file=tmp_path / "calendar" / "google_token.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        projects_dir=tmp_path / "projects",
        project_meta_dir=tmp_path / "project_meta",
        chat_sessions_dir=tmp_path / "chat_sessions",
        agents_config_path=agents_config_path,
        skills_dir=tmp_path / "skills_store",
        env_file_path=tmp_path / ".env",
        devices_file=tmp_path / "devices" / "registry.json",
        personal_graph_db_file=tmp_path / "personal_graph" / "graph.db",
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


# --- /api/workspace ----------------------------------------------------------


def test_get_workspace_reports_sandboxed_default(client, tmp_path):
    resp = client.get("/api/workspace")
    assert resp.status_code == 200
    assert resp.json()["path"] == str((tmp_path / "workspace").resolve())


def test_post_workspace_switches_and_persists(client, tmp_path):
    new_root = tmp_path / "elsewhere"
    resp = client.post("/api/workspace", json={"path": str(new_root)})
    assert resp.status_code == 200
    assert resp.json()["path"] == str(new_root.resolve())
    assert new_root.is_dir()

    saved = dotenv_values(tmp_path / ".env")
    assert saved["AURA_WORKSPACE_ROOT"] == str(new_root.resolve())
    assert client.get("/api/workspace").json()["path"] == str(new_root.resolve())


def test_post_workspace_rejects_own_project_dir(client):
    resp = client.post("/api/workspace", json={"path": str(PROJECT_ROOT)})
    assert resp.status_code == 400
    assert "AuraAgent's own project directory" in resp.json()["detail"]


# --- /api/notes ----------------------------------------------------------


def test_get_notes_reports_sandboxed_default(client, tmp_path):
    resp = client.get("/api/notes")
    assert resp.status_code == 200
    assert resp.json()["path"] == str((tmp_path / "notes").resolve())
    assert resp.json()["quick_notes_subdir"] == "Daily Notes"


def test_post_notes_switches_and_persists(client, tmp_path):
    new_root = tmp_path / "vault"
    resp = client.post("/api/notes", json={"path": str(new_root)})
    assert resp.status_code == 200
    assert resp.json()["path"] == str(new_root.resolve())

    saved = dotenv_values(tmp_path / ".env")
    assert saved["AURA_NOTES_SANDBOX_ROOT"] == str(new_root.resolve())


def test_post_quick_notes_dir_switches_and_persists(client, tmp_path):
    resp = client.post("/api/notes/quick-notes-dir", json={"subdir": "Journal/Daily"})
    assert resp.status_code == 200
    assert resp.json()["quick_notes_subdir"] == "Journal/Daily"

    saved = dotenv_values(tmp_path / ".env")
    assert saved["AURA_QUICK_NOTES_SUBDIR"] == "Journal/Daily"

    status = client.get("/api/notes").json()
    assert status["quick_notes_subdir"] == "Journal/Daily"


def test_post_quick_notes_dir_rejects_path_traversal(client):
    resp = client.post("/api/notes/quick-notes-dir", json={"subdir": "../../escape"})
    assert resp.status_code == 400
    assert "resolves outside" in resp.json()["detail"]


# --- /api/calendar ---------------------------------------------------------


def test_get_calendar_defaults_to_local(client):
    resp = client.get("/api/calendar")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "local"


def test_post_calendar_connect_without_client_secret_gives_setup_instructions(client):
    resp = client.post("/api/calendar/connect")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "console.cloud.google.com" in detail
    assert "google_client_secret.json" in detail


def test_post_calendar_disconnect_is_idempotent_when_already_local(client):
    resp = client.post("/api/calendar/disconnect")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "local"


# --- /api/projects -----------------------------------------------------------


def test_get_projects_starts_empty_with_no_active_project(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"active_slug": None, "projects": []}


def test_post_projects_creates_with_auto_directory(client, tmp_path):
    resp = client.post("/api/projects", json={"slug": "research"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "research"
    assert Path(body["directory"]).is_dir()
    assert Path(body["directory"]).is_relative_to(tmp_path / "projects")


def test_post_projects_rejects_duplicate_slug(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.post("/api/projects", json={"slug": "research"})
    assert resp.status_code == 400


def test_project_use_repoints_workspace_and_none_restores_it(client, tmp_path):
    original_workspace = client.get("/api/workspace").json()["path"]
    client.post("/api/projects", json={"slug": "research"})

    resp = client.post("/api/projects/research/use")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "research"
    assert client.get("/api/projects").json()["active_slug"] == "research"
    assert client.get("/api/workspace").json()["path"] != original_workspace

    resp = client.post("/api/projects/none")
    assert resp.status_code == 200
    assert resp.json()["active_slug"] is None
    assert client.get("/api/projects").json()["active_slug"] is None
    assert client.get("/api/workspace").json()["path"] == original_workspace


def test_project_use_unknown_slug_rejected(client):
    resp = client.post("/api/projects/nonexistent/use")
    assert resp.status_code == 404


def test_patch_project_renames_it(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.patch("/api/projects/research", json={"name": "Renamed Research"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Research"
    assert resp.json()["slug"] == "research"  # unchanged

    assert client.get("/api/projects").json()["projects"][0]["name"] == "Renamed Research"


def test_patch_project_unknown_slug_rejected(client):
    resp = client.patch("/api/projects/nonexistent", json={"name": "x"})
    assert resp.status_code == 400


# --- /api/projects/{slug}/files ---------------------------------------------


def test_get_project_files_lists_directory_contents(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    project_dir = Path(created["directory"])
    (project_dir / "sources").mkdir()
    (project_dir / "notes.md").write_text("hello", encoding="utf-8")

    resp = client.get("/api/projects/research/files")
    assert resp.status_code == 200
    entries = resp.json()
    by_name = {e["name"]: e for e in entries}
    assert by_name["sources"]["is_dir"] is True
    assert by_name["sources"]["size"] is None
    assert by_name["notes.md"]["is_dir"] is False
    assert by_name["notes.md"]["size"] == 5


def test_get_project_files_unknown_slug_rejected(client):
    resp = client.get("/api/projects/nonexistent/files")
    assert resp.status_code == 404


def test_get_project_files_rejects_path_traversal(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/files", params={"path": "../../"})
    assert resp.status_code == 400


def test_get_project_files_rejects_non_directory_path(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "notes.md").write_text("hello", encoding="utf-8")

    resp = client.get("/api/projects/research/files", params={"path": "notes.md"})
    assert resp.status_code == 400


# --- /api/projects/{slug}/files: write operations ---------------------------


def test_upload_project_file_writes_into_project_directory(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()

    resp = client.post(
        "/api/projects/research/files/upload",
        data={"path": ""},
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "notes.txt"
    assert (Path(created["directory"]) / "notes.txt").read_bytes() == b"hello world"


def test_upload_project_file_into_subdirectory(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    client.post("/api/projects/research/files/mkdir", json={"path": "docs"})

    resp = client.post(
        "/api/projects/research/files/upload",
        data={"path": "docs"},
        files={"file": ("a.txt", b"content", "text/plain")},
    )

    assert resp.status_code == 200
    assert (Path(created["directory"]) / "docs" / "a.txt").read_bytes() == b"content"


def test_upload_project_file_rejects_path_traversal(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.post(
        "/api/projects/research/files/upload",
        data={"path": "../../"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 400


def test_mkdir_project_file_creates_directory(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()

    resp = client.post("/api/projects/research/files/mkdir", json={"path": "docs/sub"})

    assert resp.status_code == 200
    assert (Path(created["directory"]) / "docs" / "sub").is_dir()


def test_mkdir_project_file_rejects_path_traversal(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.post("/api/projects/research/files/mkdir", json={"path": "../escape"})
    assert resp.status_code == 400


def test_rename_project_file_renames_it(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "old.txt").write_text("x", encoding="utf-8")

    resp = client.patch("/api/projects/research/files", json={"path": "old.txt", "new_name": "new.txt"})

    assert resp.status_code == 200
    assert not (Path(created["directory"]) / "old.txt").exists()
    assert (Path(created["directory"]) / "new.txt").is_file()


def test_rename_project_file_rejects_traversal_in_new_name(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "old.txt").write_text("x", encoding="utf-8")

    resp = client.patch("/api/projects/research/files", json={"path": "old.txt", "new_name": "../escaped.txt"})

    assert resp.status_code == 400


def test_rename_project_file_missing_source_rejected(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.patch("/api/projects/research/files", json={"path": "nope.txt", "new_name": "x.txt"})
    assert resp.status_code == 404


def test_rename_project_file_conflicting_destination_rejected(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "a.txt").write_text("a", encoding="utf-8")
    (Path(created["directory"]) / "b.txt").write_text("b", encoding="utf-8")

    resp = client.patch("/api/projects/research/files", json={"path": "a.txt", "new_name": "b.txt"})

    assert resp.status_code == 409


def test_delete_project_file_removes_it(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "gone.txt").write_text("x", encoding="utf-8")

    resp = client.delete("/api/projects/research/files", params={"path": "gone.txt"})

    assert resp.status_code == 200
    assert not (Path(created["directory"]) / "gone.txt").exists()


def test_delete_project_file_removes_directory_recursively(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    client.post("/api/projects/research/files/mkdir", json={"path": "docs"})
    (Path(created["directory"]) / "docs" / "inner.txt").write_text("x", encoding="utf-8")

    resp = client.delete("/api/projects/research/files", params={"path": "docs"})

    assert resp.status_code == 200
    assert not (Path(created["directory"]) / "docs").exists()


def test_delete_project_file_missing_path_rejected(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.delete("/api/projects/research/files", params={"path": "nope.txt"})
    assert resp.status_code == 404


def test_delete_project_file_rejects_path_traversal(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.delete("/api/projects/research/files", params={"path": "../../"})
    assert resp.status_code == 400


def test_download_project_file_returns_bytes(client):
    created = client.post("/api/projects", json={"slug": "research"}).json()
    (Path(created["directory"]) / "report.txt").write_text("the content", encoding="utf-8")

    resp = client.get("/api/projects/research/files/download", params={"path": "report.txt"})

    assert resp.status_code == 200
    assert resp.content == b"the content"


def test_download_project_file_missing_rejected(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/files/download", params={"path": "nope.txt"})
    assert resp.status_code == 404


def test_download_project_file_rejects_path_traversal(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/files/download", params={"path": "../../"})
    assert resp.status_code == 400


# --- /api/projects/{slug}/summary -------------------------------------------


def test_get_project_summary_starts_empty(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/summary")
    assert resp.status_code == 200
    assert resp.json() == {"role": "", "current_state": "", "open_questions": []}


def test_get_project_summary_unknown_slug_rejected(client):
    resp = client.get("/api/projects/nonexistent/summary")
    assert resp.status_code == 404


def test_get_project_summary_reflects_updates(client):
    client.app.state.ctx.active_project.current_slug = None  # sanity, not required by this test
    client.post("/api/projects", json={"slug": "research"})
    asyncio.run(client.app.state.ctx.project_store.update_summary("research", current_state="in progress"))

    resp = client.get("/api/projects/research/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_state"] == "in progress"


# --- /api/projects/{slug}/role ----------------------------------------------


def test_patch_project_role_sets_it(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.patch("/api/projects/research/role", json={"role": "Act as a compliance assistant"})

    assert resp.status_code == 200
    assert resp.json() == {"role": "Act as a compliance assistant"}
    summary = client.get("/api/projects/research/summary").json()
    assert summary["role"] == "Act as a compliance assistant"


def test_patch_project_role_unknown_slug_rejected(client):
    resp = client.patch("/api/projects/nonexistent/role", json={"role": "x"})
    assert resp.status_code == 404


def test_patch_project_role_on_active_project_syncs_leader_prompt(client):
    client.post("/api/projects", json={"slug": "research"})
    client.post("/api/projects/research/use")

    client.patch("/api/projects/research/role", json={"role": "Act as X"})

    assert "Act as X" in client.app.state.ctx.leader_engine.system_prompt


# --- /api/projects/{slug}/state ---------------------------------------------


def test_patch_project_state_sets_it(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.patch("/api/projects/research/state", json={"current_state": "drafted the first report"})

    assert resp.status_code == 200
    assert resp.json() == {"current_state": "drafted the first report"}
    summary = client.get("/api/projects/research/summary").json()
    assert summary["current_state"] == "drafted the first report"


def test_patch_project_state_unknown_slug_rejected(client):
    resp = client.patch("/api/projects/nonexistent/state", json={"current_state": "x"})
    assert resp.status_code == 404


def test_patch_project_state_on_active_project_syncs_leader_prompt(client):
    client.post("/api/projects", json={"slug": "research"})
    client.post("/api/projects/research/use")

    client.patch("/api/projects/research/state", json={"current_state": "drafted report.pdf"})

    assert "drafted report.pdf" in client.app.state.ctx.leader_engine.system_prompt


# --- /api/projects/{slug}/memory --------------------------------------------


def test_get_project_memory_starts_empty(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/memory")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_project_memory_unknown_slug_rejected(client):
    resp = client.get("/api/projects/nonexistent/memory")
    assert resp.status_code == 404


def test_post_project_memory_adds_a_fact(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.post("/api/projects/research/memory", json={"content": "Budget is $50k"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Budget is $50k"
    assert client.get("/api/projects/research/memory").json() == [body]


def test_patch_project_memory_updates_content(client):
    client.post("/api/projects", json={"slug": "research"})
    fact = client.post("/api/projects/research/memory", json={"content": "original"}).json()

    resp = client.patch(f"/api/projects/research/memory/{fact['id']}", json={"content": "revised"})

    assert resp.status_code == 200
    assert resp.json()["content"] == "revised"


def test_patch_project_memory_unknown_fact_rejected(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.patch("/api/projects/research/memory/nonexistent", json={"content": "x"})
    assert resp.status_code == 404


def test_delete_project_memory_removes_it(client):
    client.post("/api/projects", json={"slug": "research"})
    fact = client.post("/api/projects/research/memory", json={"content": "temp"}).json()

    resp = client.delete(f"/api/projects/research/memory/{fact['id']}")

    assert resp.status_code == 200
    assert client.get("/api/projects/research/memory").json() == []


def test_delete_project_memory_unknown_fact_rejected(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.delete("/api/projects/research/memory/nonexistent")
    assert resp.status_code == 404


def test_project_memory_is_isolated_per_project(client):
    client.post("/api/projects", json={"slug": "alpha"})
    client.post("/api/projects", json={"slug": "beta"})
    client.post("/api/projects/alpha/memory", json={"content": "fact for alpha"})

    assert client.get("/api/projects/beta/memory").json() == []


# --- /api/projects/{slug}/tools ---------------------------------------------


def test_get_project_tools_starts_with_none_enabled(client):
    client.post("/api/projects", json={"slug": "research"})
    resp = client.get("/api/projects/research/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] == []
    assert isinstance(body["available"], list)


def test_get_project_tools_unknown_slug_rejected(client):
    resp = client.get("/api/projects/nonexistent/tools")
    assert resp.status_code == 404


def test_put_project_tools_enables_and_persists(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.put("/api/projects/research/tools", json={"enabled": ["mcp_example_*"]})

    assert resp.status_code == 200
    assert resp.json() == {"enabled": ["mcp_example_*"]}
    assert client.get("/api/projects/research/tools").json()["enabled"] == ["mcp_example_*"]


def test_put_project_tools_unknown_slug_rejected(client):
    resp = client.put("/api/projects/nonexistent/tools", json={"enabled": ["x"]})
    assert resp.status_code == 404


def test_put_project_tools_on_active_project_widens_leader_view_immediately(client):
    client.post("/api/projects", json={"slug": "research"})
    client.post("/api/projects/research/use")

    client.put("/api/projects/research/tools", json={"enabled": ["mcp_example_*"]})

    ctx = client.app.state.ctx
    names = {s.name for s in ctx.leader_view.get_tool_specs()}
    assert any(n.startswith("mcp_example_") for n in names)


# --- /api/mcp --------------------------------------------------------------


def test_get_mcp_servers_lists_connected_servers_with_tool_counts(client):
    resp = client.get("/api/mcp")
    assert resp.status_code == 200
    body = resp.json()
    # The test fixture's real build_app_context() connects the shipped
    # config/mcp_servers.json "example" server for real (same as
    # test_gui_server.py's setup output shows) -- assert the shape rather
    # than a hardcoded name/count that would break if that fixture changes.
    assert isinstance(body, list)
    for entry in body:
        assert set(entry) == {"name", "tool_count"}
        assert entry["tool_count"] >= 0


# --- /api/schedules ----------------------------------------------------------


def test_get_schedules_starts_empty(client):
    resp = client.get("/api/schedules")
    assert resp.status_code == 200
    assert resp.json() == []


def test_post_schedule_once(client):
    resp = client.post(
        "/api/schedules",
        json={"task": "remind me", "trigger_type": "once", "run_at": "2026-10-05T15:00:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["trigger_type"] == "once"
    assert body["run_at"] == "2026-10-05T15:00:00"
    assert body["description"] == "2026-10-05 15:00 执行一次"
    assert body["project_slug"] is None
    assert body["enabled"] is True


def test_post_schedule_recurring(client):
    resp = client.post(
        "/api/schedules", json={"task": "daily insight", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cron_expression"] == "0 9 * * *"
    assert body["description"] == "每天 09:00"


def test_post_schedule_with_project(client):
    client.post("/api/projects", json={"slug": "research"})

    resp = client.post(
        "/api/schedules",
        json={
            "task": "x",
            "trigger_type": "recurring",
            "cron_expression": "0 9 * * *",
            "project_slug": "research",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["project_slug"] == "research"


def test_post_schedule_unknown_project_rejected(client):
    resp = client.post(
        "/api/schedules",
        json={"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project_slug": "ghost"},
    )
    assert resp.status_code == 400


def test_post_schedule_invalid_trigger_type_rejected(client):
    resp = client.post("/api/schedules", json={"task": "x", "trigger_type": "sometimes"})
    assert resp.status_code == 400


def test_post_schedule_invalid_cron_rejected(client):
    resp = client.post(
        "/api/schedules", json={"task": "x", "trigger_type": "recurring", "cron_expression": "not a cron"}
    )
    assert resp.status_code == 400


def test_get_schedules_filters_by_project(client):
    client.post("/api/projects", json={"slug": "p1"})
    client.post("/api/projects", json={"slug": "p2"})
    client.post(
        "/api/schedules",
        json={"task": "a", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project_slug": "p1"},
    )
    client.post(
        "/api/schedules",
        json={"task": "b", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project_slug": "p2"},
    )

    resp = client.get("/api/schedules", params={"project": "p1"})

    assert resp.status_code == 200
    tasks = [s["task"] for s in resp.json()]
    assert tasks == ["a"]


def test_patch_schedule_updates_task(client):
    created = client.post(
        "/api/schedules", json={"task": "old", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    ).json()

    resp = client.patch(f"/api/schedules/{created['id']}", json={"task": "new"})

    assert resp.status_code == 200
    assert resp.json()["task"] == "new"


def test_patch_schedule_toggles_enabled(client):
    created = client.post(
        "/api/schedules", json={"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    ).json()
    assert created["enabled"] is True

    resp = client.patch(f"/api/schedules/{created['id']}", json={"enabled": False})

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    # Persisted, not just returned -- a follow-up GET sees it too.
    listed = client.get("/api/schedules").json()
    assert listed[0]["enabled"] is False


def test_patch_schedule_clears_project_slug(client):
    client.post("/api/projects", json={"slug": "research"})
    created = client.post(
        "/api/schedules",
        json={"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project_slug": "research"},
    ).json()

    resp = client.patch(f"/api/schedules/{created['id']}", json={"project_slug": None})

    assert resp.status_code == 200
    assert resp.json()["project_slug"] is None


def test_patch_schedule_switches_trigger_type(client):
    created = client.post(
        "/api/schedules", json={"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    ).json()

    resp = client.patch(f"/api/schedules/{created['id']}", json={"run_at": "2026-10-05T15:00:00"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["trigger_type"] == "once"
    assert body["run_at"] == "2026-10-05T15:00:00"
    assert body["cron_expression"] is None


def test_patch_schedule_unknown_id_rejected(client):
    resp = client.patch("/api/schedules/nonexistent", json={"task": "x"})
    assert resp.status_code == 404


def test_delete_schedule_removes_it(client):
    created = client.post(
        "/api/schedules", json={"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    ).json()

    resp = client.delete(f"/api/schedules/{created['id']}")

    assert resp.status_code == 200
    assert client.get("/api/schedules").json() == []


def test_delete_schedule_unknown_id_rejected(client):
    resp = client.delete("/api/schedules/nonexistent")
    assert resp.status_code == 404


# --- /api/sessions -------------------------------------------------------
# Sessions are created over the WebSocket (gui/server.py, on connect or via
# "new_chat" -- see tests/test_gui_server.py), not through REST, so setup
# here goes straight through the store; these tests only exercise the
# read/rename/delete REST surface itself.


def test_get_sessions_starts_empty(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_sessions_lists_most_recently_updated_first(client):
    store = client.app.state.session_store

    first = asyncio.run(store.create_session())
    second = asyncio.run(store.create_session())
    asyncio.run(store.touch(first.id))

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()] == [first.id, second.id]


def test_get_sessions_unaffiliated_excludes_project_tagged_chats(client):
    store = client.app.state.session_store
    client.post("/api/projects", json={"slug": "climate"})
    tagged = asyncio.run(store.create_session(project_slug="climate"))
    plain = asyncio.run(store.create_session())

    resp = client.get("/api/sessions", params={"unaffiliated": "true"})
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert plain.id in ids
    assert tagged.id not in ids


def test_patch_session_renames_it(client):
    store = client.app.state.session_store

    info = asyncio.run(store.create_session())

    resp = client.patch(f"/api/sessions/{info.id}", json={"title": "Renamed chat"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed chat"
    assert client.get("/api/sessions").json()[0]["title"] == "Renamed chat"


def test_patch_session_unknown_id_rejected(client):
    resp = client.patch("/api/sessions/nonexistent", json={"title": "x"})
    assert resp.status_code == 400


def test_delete_session_removes_it(client):
    store = client.app.state.session_store

    info = asyncio.run(store.create_session())

    resp = client.delete(f"/api/sessions/{info.id}")
    assert resp.status_code == 200
    assert client.get("/api/sessions").json() == []


def test_delete_session_unknown_id_rejected(client):
    resp = client.delete("/api/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_active_session_is_rejected(client):
    store = client.app.state.session_store

    info = asyncio.run(store.create_session())
    client.app.state.session_sink.active_session_id = info.id

    resp = client.delete(f"/api/sessions/{info.id}")
    assert resp.status_code == 400
    assert "active chat" in resp.json()["detail"]


def test_pin_and_unpin_session(client):
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())

    resp = client.post(f"/api/sessions/{info.id}/pin")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True
    assert client.get("/api/sessions").json()[0]["pinned"] is True

    resp = client.post(f"/api/sessions/{info.id}/unpin")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is False


def test_pin_unknown_session_rejected(client):
    resp = client.post("/api/sessions/nonexistent/pin")
    assert resp.status_code == 404


def test_set_session_project_tags_and_validates(client):
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())
    client.post("/api/projects", json={"slug": "climate"})

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": "climate"})
    assert resp.status_code == 200
    assert resp.json()["project_slug"] == "climate"

    resp = client.get("/api/sessions", params={"project": "climate"})
    assert [s["id"] for s in resp.json()] == [info.id]

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": None})
    assert resp.status_code == 200
    assert resp.json()["project_slug"] is None


def test_set_session_project_unknown_project_rejected(client):
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": "nonexistent"})
    assert resp.status_code == 404


def test_set_session_project_unknown_session_rejected(client):
    resp = client.post("/api/sessions/nonexistent/project", json={"slug": None})
    assert resp.status_code == 404


def test_set_session_project_does_not_eagerly_sync_live_state(client):
    # N14: a REST request has no connection of its own, so this endpoint
    # cannot safely guess "is this the chat some WebSocket connection
    # happens to have open right now" without risking leaking the change
    # into a different connection's in-flight turn (session_sink
    # .active_session_id is a process-wide pointer, not scoped to this
    # request -- and with Auralis now a real second connection, "a
    # different connection" is a real case, not a hypothetical one). Only
    # the tag is persisted -- gui/server.py's _run_orchestrator() picks it
    # up fresh at the start of the next turn on whichever connection
    # actually has this chat open (see test_gui_server.py for that path).
    client.post("/api/projects", json={"slug": "climate"})
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": "climate"})
    assert resp.status_code == 200
    assert resp.json()["project_slug"] == "climate"
    assert client.app.state.ctx.active_project.current_slug is None


def test_set_session_directory_sets_and_clears(client, tmp_path):
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())
    new_dir = tmp_path / "elsewhere"

    resp = client.post(f"/api/sessions/{info.id}/directory", json={"directory": str(new_dir)})
    assert resp.status_code == 200
    assert resp.json()["directory"] == str(new_dir.resolve())
    assert new_dir.is_dir()

    resp = client.post(f"/api/sessions/{info.id}/directory", json={"directory": None})
    assert resp.status_code == 200
    assert resp.json()["directory"] is None


def test_set_session_directory_rejects_a_windows_system_directory(client):
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())

    resp = client.post(f"/api/sessions/{info.id}/directory", json={"directory": r"C:\Windows\System32"})
    assert resp.status_code == 400
    assert "Windows system directory" in resp.json()["detail"]


def test_set_session_directory_unknown_session_rejected(client):
    resp = client.post("/api/sessions/nonexistent/directory", json={"directory": None})
    assert resp.status_code == 404


def test_set_session_directory_does_not_eagerly_sync_live_state(client, tmp_path):
    # N14: same reasoning as test_set_session_project_does_not_eagerly_sync_live_state.
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())
    new_dir = tmp_path / "elsewhere"

    resp = client.post(f"/api/sessions/{info.id}/directory", json={"directory": str(new_dir)})
    assert resp.status_code == 200

    assert client.app.state.ctx.workspace_root.current != new_dir.resolve()


def test_directory_survives_leaving_a_project(client, tmp_path):
    # Real behavior this documents: a chat's own directory is never
    # cleared as a side effect of joining/leaving a Project -- it just
    # silently resumes taking effect (via gui/server.py's per-turn resync,
    # not tested here -- see cli/service.py::sync_active_directory()'s own
    # priority-chain tests) once the Project tag is removed.
    client.post("/api/projects", json={"slug": "climate"})
    store = client.app.state.session_store
    info = asyncio.run(store.create_session())
    own_dir = tmp_path / "own"
    client.post(f"/api/sessions/{info.id}/directory", json={"directory": str(own_dir)})

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": "climate"})
    assert resp.json()["directory"] == str(own_dir.resolve())

    resp = client.post(f"/api/sessions/{info.id}/project", json={"slug": None})
    assert resp.json()["directory"] == str(own_dir.resolve())
