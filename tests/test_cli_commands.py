"""Tests for cli/commands.py — the human-direct "/" command layer.
Builds a real CLIContext (real ToolRegistry/AgentRegistry/ScopedToolRegistryView/
SkillLoader, FakeLLMProvider under a real SwappableProvider) so these
exercise the exact same construction/persistence helpers main.py wires up,
without ever touching the real project's config/agents.json or .env.
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents.agent_registry import AgentRegistry
from cli import service
from cli.commands import dispatch_command, is_command
from cli.context import CLIContext
from core.react_engine import AsyncReActEngine
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tests.fakes import FakeLLMProvider, make_test_logger
from tools.base import ToolSpec
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.calendar.swappable_calendar_provider import SwappableCalendarProvider
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
from tools.workspace_root import SwappableWorkspaceRoot

_SKILL_MD = "---\nname: demo_skill\ndescription: a demo\n---\nbody\n"
_RUN_PY = (
    "import argparse\n"
    "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
    "p.parse_args()\n"
    "print('ok')\n"
)

async def _dummy_handler(args):
    return "ok"

def _write_agents_config(tmp_path: Path) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps({"agents": [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]}),
        encoding="utf-8",
    )
    return path

def _build_ctx(tmp_path: Path, provider_responses=None) -> CLIContext:
    agents_config_path = _write_agents_config(tmp_path)
    agent_registry = AgentRegistry.load(agents_config_path)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="calculate", description="adds", input_schema={"type": "object", "properties": {}}),
        _dummy_handler,
    )

    from agents.scoped_tool_registry import ScopedToolRegistryView

    leader_view = ScopedToolRegistryView(registry, agent_registry.leader.capabilities)

    provider = SwappableProvider(FakeLLMProvider(provider_responses or []), "anthropic")
    logger = make_test_logger(tmp_path / "logs")

    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    skill_loader = SkillLoader(skills_dir, registry)

    mcp_manager = MCPClientManager(tmp_path / "mcp_servers.json", registry)

    settings = SimpleNamespace(
        agents_config_path=agents_config_path,
        skills_dir=skills_dir,
        openai_base_url=None,
        calendar_events_file=tmp_path / "calendar" / "events.json",
        google_client_secret_file=tmp_path / "calendar" / "google_client_secret.json",
        google_token_file=tmp_path / "calendar" / "google_token.json",
        projects_dir=tmp_path / "projects",
    )

    base_leader_system_prompt = "x"
    leader_engine = AsyncReActEngine(
        provider=provider, registry=leader_view, logger=logger,
        system_prompt=base_leader_system_prompt, max_turns=5, agent_name="orchestrator",
    )
    project_store = ProjectStore(
        registry_path=tmp_path / "project_meta" / "registry.json",
        meta_dir=tmp_path / "project_meta",
        summary_max_chars=4_000,
    )

    return CLIContext(
        settings=settings,
        registry=registry,
        agent_registry=agent_registry,
        leader_view=leader_view,
        provider=provider,
        logger=logger,
        max_turns=5,
        skill_loader=skill_loader,
        mcp_manager=mcp_manager,
        http_client=httpx.AsyncClient(),
        agents_config_lock=asyncio.Lock(),
        workspace_root=SwappableWorkspaceRoot(tmp_path / "workspace"),
        notes_root=SwappableWorkspaceRoot(tmp_path / "notes"),
        calendar_provider=SwappableCalendarProvider(
            LocalJSONCalendarProvider(tmp_path / "calendar" / "events.json"), "local"
        ),
        project_store=project_store,
        active_project=ActiveProjectState(),
        leader_engine=leader_engine,
        base_leader_system_prompt=base_leader_system_prompt,
        known_api_keys={"anthropic": "sk-ant-abcdef0000", "openai": ""},
        env_file_path=tmp_path / ".env",
    )

def _queued_input(monkeypatch, *answers: str):
    remaining = list(answers)

    def _fake_input(prompt: str = "") -> str:
        if not remaining:
            raise AssertionError(f"input() called with no more queued answers (prompt={prompt!r})")
        return remaining.pop(0)

    monkeypatch.setattr("builtins.input", _fake_input)
    return remaining

def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()

# --- is_command / /help -----------------------------------------------------

def test_is_command():
    assert is_command("/help") is True
    assert is_command("hello") is False

@pytest.mark.asyncio
async def test_help_prints_command_list(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/help", ctx)
    out = capsys.readouterr().out
    assert "/config" in out and "/agents" in out and "/skills" in out

@pytest.mark.asyncio
async def test_unknown_command_reports_an_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/bogus", ctx)
    assert "Unknown command" in capsys.readouterr().out

# --- /config -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_show_masks_the_key(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/config", ctx)
    out = capsys.readouterr().out
    assert "provider=anthropic" in out
    assert "sk-ant-abcdef0000" not in out
    assert "sk...00" in out

@pytest.mark.asyncio
async def test_config_use_unknown_provider_rejected(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/config use bogus some-model", ctx)
    assert "Unknown provider" in capsys.readouterr().out
    assert ctx.provider.provider_name == "anthropic"

@pytest.mark.asyncio
async def test_config_use_without_a_known_key_is_rejected(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/config use openai deepseek-chat", ctx)
    assert "No API key known" in capsys.readouterr().out
    assert ctx.provider.provider_name == "anthropic"

@pytest.mark.asyncio
async def test_config_use_switches_the_swappable_provider(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    ctx.known_api_keys["openai"] = "sk-openai-test0000"

    await dispatch_command("/config use openai deepseek-chat", ctx)

    assert ctx.provider.provider_name == "openai"
    assert ctx.provider.model_name == "deepseek-chat"
    assert "Switched" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_config_set_key_unknown_provider_rejected(tmp_path):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/config set-key bogus", ctx)
    assert not ctx.env_file_path.exists()

@pytest.mark.asyncio
async def test_config_set_key_saves_to_env_file_and_memory(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "brand-new-key")

    await dispatch_command("/config set-key openai", ctx)

    assert ctx.known_api_keys["openai"] == "brand-new-key"
    assert "OPENAI_API_KEY" in ctx.env_file_path.read_text(encoding="utf-8")
    assert "brand-new-key" in ctx.env_file_path.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "brand-new-key" not in out  # never echoed to the terminal

@pytest.mark.asyncio
async def test_config_set_key_empty_input_changes_nothing(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "   ")

    await dispatch_command("/config set-key anthropic", ctx)

    assert ctx.known_api_keys["anthropic"] == "sk-ant-abcdef0000"  # unchanged
    assert not ctx.env_file_path.exists()

# --- /agents -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_agents_list_shows_leader_and_workers(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/agents", ctx)
    assert "orchestrator (leader)" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_agents_add_creates_a_working_delegate_tool_and_persists(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path, provider_responses=[])
    _queued_input(monkeypatch, "analyst", "You are an analyst.", "calculate", "y")

    await dispatch_command("/agents add", ctx)

    assert ctx.agent_registry.get("analyst").name == "analyst"
    assert "delegate_to_analyst" in {s.name for s in ctx.leader_view.get_tool_specs()}
    saved = json.loads(ctx.settings.agents_config_path.read_text(encoding="utf-8"))
    assert {a["name"] for a in saved["agents"]} == {"orchestrator", "analyst"}

@pytest.mark.asyncio
async def test_agents_add_declined_makes_no_changes(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    _queued_input(monkeypatch, "analyst", "You are an analyst.", "calculate", "n")

    await dispatch_command("/agents add", ctx)

    with pytest.raises(Exception):
        ctx.agent_registry.get("analyst")
    saved = json.loads(ctx.settings.agents_config_path.read_text(encoding="utf-8"))
    assert [a["name"] for a in saved["agents"]] == ["orchestrator"]

@pytest.mark.asyncio
async def test_agents_add_rejects_invalid_name_before_confirming(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    remaining = _queued_input(monkeypatch, "not a valid name!", "sys", "calculate")

    await dispatch_command("/agents add", ctx)

    assert "Invalid agent definition" in capsys.readouterr().out
    assert remaining == []  # never reached the confirm prompt

@pytest.mark.asyncio
async def test_agents_add_rejects_name_collision(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    _queued_input(monkeypatch, "orchestrator", "sys", "calculate")

    await dispatch_command("/agents add", ctx)

    assert "already exists" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_agents_remove_removes_worker_and_delegate_tool(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    _queued_input(monkeypatch, "analyst", "You are an analyst.", "calculate", "y")
    await dispatch_command("/agents add", ctx)

    await dispatch_command("/agents remove analyst", ctx)

    with pytest.raises(Exception):
        ctx.agent_registry.get("analyst")
    assert "delegate_to_analyst" not in {s.name for s in ctx.leader_view.get_tool_specs()}
    saved = json.loads(ctx.settings.agents_config_path.read_text(encoding="utf-8"))
    assert [a["name"] for a in saved["agents"]] == ["orchestrator"]

@pytest.mark.asyncio
async def test_agents_remove_unknown_name_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/agents remove nonexistent", ctx)
    assert "No agent named" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_agents_remove_leader_is_rejected(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/agents remove orchestrator", ctx)
    assert "Cannot remove the leader" in capsys.readouterr().out

# --- /skills -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_skills_list_starts_empty_then_shows_installed_skill(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/skills", ctx)
    assert capsys.readouterr().out.strip() == ""

    local_zip = tmp_path / "pkg.zip"
    local_zip.write_bytes(_make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY}))
    _queued_input(monkeypatch, "y")
    await dispatch_command(f"/skills install {local_zip}", ctx)
    capsys.readouterr()  # drain

    await dispatch_command("/skills", ctx)
    assert "demo_skill" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_skills_install_from_local_zip_hot_registers_and_persists(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    local_zip = tmp_path / "pkg.zip"
    local_zip.write_bytes(_make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY}))
    _queued_input(monkeypatch, "y")

    await dispatch_command(f"/skills install {local_zip}", ctx)

    assert (ctx.settings.skills_dir / "demo_skill" / "run.py").is_file()
    result = await ctx.registry.dispatch("demo_skill", {})
    assert result == "ok"
    saved = json.loads(ctx.settings.agents_config_path.read_text(encoding="utf-8"))
    assert "demo_skill" in saved["agents"][0]["capabilities"]

@pytest.mark.asyncio
async def test_skills_install_declined_writes_no_files(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    local_zip = tmp_path / "pkg.zip"
    local_zip.write_bytes(_make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY}))
    _queued_input(monkeypatch, "n")

    await dispatch_command(f"/skills install {local_zip}", ctx)

    assert not (ctx.settings.skills_dir / "demo_skill").exists()

@pytest.mark.asyncio
async def test_skills_install_missing_file_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command(f"/skills install {tmp_path / 'nope.zip'}", ctx)
    assert "No such file" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_skills_install_malformed_package_rejected_before_any_prompt(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    local_zip = tmp_path / "bad.zip"
    local_zip.write_bytes(_make_zip_bytes({"SKILL.md": _SKILL_MD}))  # missing run.py

    def _fail_if_called(prompt=""):
        raise AssertionError("input() should not be called for a structurally invalid package")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    await dispatch_command(f"/skills install {local_zip}", ctx)

    assert "Rejected" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_skills_install_name_collision_rejected_before_prompting(tmp_path, monkeypatch, capsys):
    ctx = _build_ctx(tmp_path)
    local_zip = tmp_path / "pkg.zip"
    local_zip.write_bytes(_make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY}))
    _queued_input(monkeypatch, "y")
    await dispatch_command(f"/skills install {local_zip}", ctx)
    capsys.readouterr()

    def _fail_if_called(prompt=""):
        raise AssertionError("input() should not be called for a name collision")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    await dispatch_command(f"/skills install {local_zip}", ctx)

    assert "already exists" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_skills_load_rejects_non_http_urls_without_any_network_call(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/skills load file:///etc/passwd", ctx)
    assert "Only http" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_skills_load_downloads_and_installs_over_http(tmp_path, monkeypatch):
    zip_bytes = _make_zip_bytes({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_bytes)

    ctx = _build_ctx(tmp_path)
    ctx.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _queued_input(monkeypatch, "y")

    await dispatch_command("/skills load https://example.com/pkg.zip", ctx)

    assert (ctx.settings.skills_dir / "demo_skill").is_dir()

@pytest.mark.asyncio
async def test_skills_load_reports_download_failure(tmp_path, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    ctx = _build_ctx(tmp_path)
    ctx.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await dispatch_command("/skills load https://example.com/missing.zip", ctx)

    assert "Download failed" in capsys.readouterr().out

# --- /workspace ----------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_show_prints_current_path(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/workspace", ctx)
    assert str(ctx.workspace_root.current) in capsys.readouterr().out

@pytest.mark.asyncio
async def test_workspace_set_switches_and_persists_to_env(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    new_root = tmp_path / "elsewhere"

    await dispatch_command(f"/workspace set {new_root}", ctx)

    assert ctx.workspace_root.current == new_root.resolve()
    assert new_root.is_dir()
    # dotenv's set_key() escapes backslashes in the raw file text (so its
    # own parser round-trips them correctly) -- read it back through
    # dotenv itself rather than substring-matching the raw file content.
    from dotenv import dotenv_values

    saved = dotenv_values(ctx.env_file_path)
    assert saved["AURA_WORKSPACE_ROOT"] == str(new_root.resolve())
    assert "Workspace set" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_workspace_set_creates_a_nonexistent_directory(tmp_path):
    ctx = _build_ctx(tmp_path)
    new_root = tmp_path / "does" / "not" / "exist" / "yet"

    await dispatch_command(f"/workspace set {new_root}", ctx)

    assert new_root.is_dir()

@pytest.mark.asyncio
async def test_workspace_set_rejects_a_windows_system_directory(tmp_path, capsys):
    # Windows-specific: _windows_blocked_roots() (cli/service.py) resolves
    # to an empty set on other platforms, so this only exercises the real
    # rejection path on the Windows machine this project is developed on.
    ctx = _build_ctx(tmp_path)
    original = ctx.workspace_root.current

    await dispatch_command(r"/workspace set C:\Windows\System32", ctx)

    assert ctx.workspace_root.current == original  # unchanged
    out = capsys.readouterr().out
    assert "Windows system directory" in out

@pytest.mark.asyncio
async def test_workspace_set_rejects_auraagents_own_project_directory(tmp_path, capsys):
    from config.settings import PROJECT_ROOT

    ctx = _build_ctx(tmp_path)
    original = ctx.workspace_root.current

    await dispatch_command(f"/workspace set {PROJECT_ROOT}", ctx)

    assert ctx.workspace_root.current == original  # unchanged
    assert "project directory" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_workspace_set_missing_argument_shows_usage(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/workspace set", ctx)
    assert "Usage" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_workspace_set_nonexistent_drive_letter_reports_clearly_not_a_raw_traceback(tmp_path, capsys):
    # Real bug this guards against: Path.mkdir(parents=True) can only
    # create directories on an already-mounted volume, so pointing at a
    # drive letter that doesn't exist at all raises a raw
    # FileNotFoundError (WinError 3) -- previously this propagated
    # unhandled instead of the clean ValueError every other rejected path
    # gets in this command.
    import string

    unused_letter = next(letter for letter in string.ascii_uppercase if not Path(f"{letter}:\\").exists())

    ctx = _build_ctx(tmp_path)
    original = ctx.workspace_root.current

    await dispatch_command(f"/workspace set {unused_letter}:\\some\\path", ctx)

    assert ctx.workspace_root.current == original  # unchanged
    out = capsys.readouterr().out
    assert "Could not create or access" in out
    assert "Traceback" not in out

# --- /notes ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_notes_show_prints_current_path(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/notes", ctx)
    assert str(ctx.notes_root.current) in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_set_switches_and_persists_to_env(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    new_root = tmp_path / "obsidian-vault"

    await dispatch_command(f"/notes set {new_root}", ctx)

    assert ctx.notes_root.current == new_root.resolve()
    assert new_root.is_dir()
    from dotenv import dotenv_values

    saved = dotenv_values(ctx.env_file_path)
    assert saved["AURA_NOTES_SANDBOX_ROOT"] == str(new_root.resolve())
    assert "Notes root set" in capsys.readouterr().out
    # Independent from workspace_root -- switching notes must not touch it.
    assert ctx.workspace_root.current != new_root.resolve()

@pytest.mark.asyncio
async def test_notes_set_rejects_a_windows_system_directory(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    original = ctx.notes_root.current

    await dispatch_command(r"/notes set C:\Windows\System32", ctx)

    assert ctx.notes_root.current == original  # unchanged
    assert "Windows system directory" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_set_rejects_auraagents_own_project_directory(tmp_path, capsys):
    from config.settings import PROJECT_ROOT

    ctx = _build_ctx(tmp_path)
    original = ctx.notes_root.current

    await dispatch_command(f"/notes set {PROJECT_ROOT}", ctx)

    assert ctx.notes_root.current == original  # unchanged
    assert "project directory" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_set_missing_argument_shows_usage(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/notes set", ctx)
    assert "Usage" in capsys.readouterr().out

# --- /calendar -------------------------------------------------------------

@pytest.mark.asyncio
async def test_calendar_show_prints_current_backend(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/calendar", ctx)
    assert "backend=local" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_calendar_connect_without_client_secret_prints_setup_instructions(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)

    await dispatch_command("/calendar connect", ctx)

    out = capsys.readouterr().out
    assert "console.cloud.google.com" in out
    assert ctx.calendar_provider.backend_name == "local"  # unchanged

@pytest.mark.asyncio
async def test_calendar_connect_succeeds_and_hot_swaps_immediately(tmp_path, capsys, monkeypatch):
    ctx = _build_ctx(tmp_path)
    ctx.settings.google_client_secret_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings.google_client_secret_file.write_text("{}", encoding="utf-8")

    def fake_run_oauth_flow(client_secret_file, token_file):
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(
            json.dumps(
                {
                    "refresh_token": "fake-refresh-token",
                    "client_id": "fake-client-id",
                    "client_secret": "fake-client-secret",
                    "token": "fake-access-token",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "scopes": ["https://www.googleapis.com/auth/calendar.events"],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr("tools.calendar.google_auth.run_oauth_flow", fake_run_oauth_flow)

    await dispatch_command("/calendar connect", ctx)

    out = capsys.readouterr().out
    assert "Connected" in out
    assert ctx.calendar_provider.backend_name == "google"  # hot-swapped, no restart
    from dotenv import dotenv_values

    saved = dotenv_values(ctx.env_file_path)
    assert saved["AURA_CALENDAR_BACKEND"] == "google"

@pytest.mark.asyncio
async def test_calendar_connect_never_opens_a_real_browser(tmp_path, monkeypatch):
    # Guards against a regression where run_oauth_flow's real implementation
    # (which opens a browser) accidentally runs during the test suite.
    ctx = _build_ctx(tmp_path)
    ctx.settings.google_client_secret_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings.google_client_secret_file.write_text("{}", encoding="utf-8")
    called = {"count": 0}

    def fake_run_oauth_flow(client_secret_file, token_file):
        called["count"] += 1
        raise RuntimeError("simulated failure, never a real browser flow")

    monkeypatch.setattr("tools.calendar.google_auth.run_oauth_flow", fake_run_oauth_flow)

    await dispatch_command("/calendar connect", ctx)

    assert called["count"] == 1
    assert ctx.calendar_provider.backend_name == "local"  # failed cleanly, no partial switch

@pytest.mark.asyncio
async def test_calendar_disconnect_switches_back_to_local(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    from tools.calendar.google_calendar_provider import GoogleCalendarProvider

    class _FakeCreds:
        expired = False
        token = "x"

    ctx.calendar_provider.set_current(
        GoogleCalendarProvider(_FakeCreds(), ctx.settings.google_token_file, ctx.http_client), "google"
    )

    await dispatch_command("/calendar disconnect", ctx)

    assert ctx.calendar_provider.backend_name == "local"
    assert "Disconnected" in capsys.readouterr().out
    from dotenv import dotenv_values

    saved = dotenv_values(ctx.env_file_path)
    assert saved["AURA_CALENDAR_BACKEND"] == "local"

# --- /project ----------------------------------------------------------

@pytest.mark.asyncio
async def test_project_list_when_empty(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project", ctx)
    assert "No projects yet" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_create_default_directory(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create ai_governance", ctx)
    out = capsys.readouterr().out
    assert "Created project 'ai_governance'" in out
    assert (tmp_path / "projects" / "ai_governance").is_dir()

@pytest.mark.asyncio
async def test_project_create_custom_existing_directory(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    custom = tmp_path / "elsewhere" / "my vault"

    await dispatch_command(f"/project create enoch {custom}", ctx)

    assert custom.is_dir()
    status = await service.get_project_status(ctx)
    assert status.projects[0].directory == custom.resolve()

@pytest.mark.asyncio
async def test_project_create_rejects_windows_system_directory(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command(r"/project create bad C:\Windows\System32", ctx)
    assert "Windows system directory" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_create_duplicate_slug_rejected(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create dup", ctx)
    capsys.readouterr()
    await dispatch_command("/project create dup", ctx)
    assert "already exists" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_use_switches_workspace_and_loads_summary(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create ai_governance", ctx)
    project_dir = tmp_path / "projects" / "ai_governance"
    await ctx.project_store.update_summary("ai_governance", current_state="tracking EU AI Act")
    capsys.readouterr()

    await dispatch_command("/project use ai_governance", ctx)

    assert ctx.workspace_root.current == project_dir.resolve()
    assert "tracking EU AI Act" in ctx.leader_engine.system_prompt
    assert ctx.leader_engine.system_prompt.startswith(ctx.base_leader_system_prompt)
    assert "Entered project 'ai_governance'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_use_unknown_slug_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project use nope", ctx)
    assert "No project named 'nope'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_switch_mid_session_between_two_projects(tmp_path):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project create beta", ctx)
    await ctx.project_store.update_summary("alpha", current_state="alpha state")
    await ctx.project_store.update_summary("beta", current_state="beta state")

    await dispatch_command("/project use alpha", ctx)
    assert ctx.workspace_root.current == (tmp_path / "projects" / "alpha").resolve()
    assert "alpha state" in ctx.leader_engine.system_prompt

    await dispatch_command("/project use beta", ctx)
    assert ctx.workspace_root.current == (tmp_path / "projects" / "beta").resolve()
    assert "beta state" in ctx.leader_engine.system_prompt
    assert "alpha state" not in ctx.leader_engine.system_prompt  # not stacked/leaked from the previous project

@pytest.mark.asyncio
async def test_project_none_restores_original_workspace_and_clears_prompt(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    original_workspace = ctx.workspace_root.current
    await dispatch_command("/project create alpha", ctx)
    await ctx.project_store.update_summary("alpha", current_state="alpha state")
    await dispatch_command("/project use alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project none", ctx)

    assert ctx.workspace_root.current == original_workspace
    assert ctx.leader_engine.system_prompt == ctx.base_leader_system_prompt
    assert "Left the active project" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_use_does_not_persist_to_env(tmp_path):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project use alpha", ctx)

    assert not ctx.env_file_path.exists()  # /project never calls set_key()
