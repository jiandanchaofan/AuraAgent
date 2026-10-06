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
from tools.devices.device_store import DeviceStore
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.swappable_task_provider import SwappableTaskProvider
from tools.personal_graph.graph_store import GraphStore
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.scheduler.schedule_store import ScheduleStore
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
        llm_max_output_tokens=8192,
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
        default_workspace_root=SwappableWorkspaceRoot(tmp_path / "workspace"),
        notes_root=SwappableWorkspaceRoot(tmp_path / "notes"),
        quick_notes_subdir="Daily Notes",
        calendar_provider=SwappableCalendarProvider(
            LocalJSONCalendarProvider(tmp_path / "calendar" / "events.json"), "local"
        ),
        task_provider=SwappableTaskProvider(
            LocalJSONTaskProvider(tmp_path / "tasks" / "tasks.json"), "local"
        ),
        project_store=project_store,
        active_project=ActiveProjectState(),
        leader_engine=leader_engine,
        base_leader_system_prompt=base_leader_system_prompt,
        schedule_store=ScheduleStore(tmp_path / "schedules.json"),
        device_store=DeviceStore(tmp_path / "devices.json"),
        graph_store=GraphStore(tmp_path / "graph.db"),
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

@pytest.mark.asyncio
async def test_workspace_set_while_project_active_does_not_disturb_the_project(tmp_path, capsys):
    # Real bug this guards against: /workspace set and /project use used
    # to silently fight over the exact same shared workspace_root, with
    # no coordination -- running /workspace set while a Project was active
    # would leave the UI/system prompt still reporting the Project as
    # active while every file tool had actually moved somewhere else.
    # sync_active_directory()'s priority chain (Project > Chat directory >
    # default) makes this impossible by construction: the live directory
    # only ever changes here if no Project governs it.
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project use alpha", ctx)
    project_dir = (tmp_path / "projects" / "alpha").resolve()
    assert ctx.workspace_root.current == project_dir
    capsys.readouterr()
    new_default = tmp_path / "elsewhere"

    await dispatch_command(f"/workspace set {new_default}", ctx)

    # The Project is still fully active and the live directory untouched.
    assert ctx.active_project.current_slug == "alpha"
    assert ctx.workspace_root.current == project_dir
    out = capsys.readouterr().out
    assert "won't take effect until" in out

    # But the new default WAS saved, and takes effect once the Project is left.
    await dispatch_command("/project none", ctx)
    assert ctx.workspace_root.current == new_default.resolve()

@pytest.mark.asyncio
async def test_workspace_set_with_no_project_active_still_applies_immediately(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    new_root = tmp_path / "elsewhere"

    await dispatch_command(f"/workspace set {new_root}", ctx)

    assert ctx.workspace_root.current == new_root.resolve()
    out = capsys.readouterr().out
    assert "won't take effect" not in out

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

@pytest.mark.asyncio
async def test_notes_show_prints_quick_notes_subdir(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/notes", ctx)
    assert "Daily Notes" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_quickdir_switches_and_persists_to_env(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)

    await dispatch_command("/notes quickdir Journal/Daily", ctx)

    assert ctx.quick_notes_subdir == "Journal/Daily"
    from dotenv import dotenv_values

    saved = dotenv_values(ctx.env_file_path)
    assert saved["AURA_QUICK_NOTES_SUBDIR"] == "Journal/Daily"
    assert "Quick notes subdirectory set" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_quickdir_rejects_path_traversal(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    original = ctx.quick_notes_subdir

    await dispatch_command("/notes quickdir ../../escape", ctx)

    assert ctx.quick_notes_subdir == original  # unchanged
    assert "resolves outside" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_notes_quickdir_missing_argument_shows_usage(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/notes quickdir", ctx)
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

@pytest.mark.asyncio
async def test_project_rename_changes_display_name_not_slug(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project rename alpha Alpha Team Research", ctx)
    assert "Renamed 'alpha' to 'Alpha Team Research'" in capsys.readouterr().out

    status = await service.get_project_status(ctx)
    assert status.projects[0].slug == "alpha"
    assert status.projects[0].name == "Alpha Team Research"

@pytest.mark.asyncio
async def test_project_rename_unknown_slug_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project rename nope New Name", ctx)
    assert "No project named 'nope'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_rename_missing_name_shows_usage(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project rename alpha", ctx)
    assert "Usage: /project rename" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_role_sets_and_syncs_active_prompt(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project use alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project role alpha Act as a compliance assistant", ctx)

    assert "Updated role for 'alpha'" in capsys.readouterr().out
    assert "Act as a compliance assistant" in ctx.leader_engine.system_prompt

@pytest.mark.asyncio
async def test_project_role_on_inactive_project_does_not_touch_prompt(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project create beta", ctx)
    await dispatch_command("/project use alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project role beta Act as X", ctx)

    assert "Act as X" not in ctx.leader_engine.system_prompt

@pytest.mark.asyncio
async def test_project_role_unknown_slug_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project role nope some text", ctx)
    assert "No project named 'nope'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_state_sets_and_syncs_active_prompt(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project use alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project state alpha drafted the first report.pdf", ctx)

    assert "Updated current state for 'alpha'" in capsys.readouterr().out
    assert "drafted the first report.pdf" in ctx.leader_engine.system_prompt

@pytest.mark.asyncio
async def test_project_state_on_inactive_project_does_not_touch_prompt(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project create beta", ctx)
    await dispatch_command("/project use alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project state beta drafted X", ctx)

    assert "drafted X" not in ctx.leader_engine.system_prompt

@pytest.mark.asyncio
async def test_project_state_unknown_slug_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project state nope some text", ctx)
    assert "No project named 'nope'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_memory_add_list_and_delete(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project memory alpha add The client's budget is $50k", ctx)
    assert "Remembered" in capsys.readouterr().out

    await dispatch_command("/project memory alpha", ctx)
    listed = capsys.readouterr().out
    assert "$50k" in listed

    fact_id = listed.strip().split("id=")[1].split(")")[0]
    await dispatch_command(f"/project memory alpha delete {fact_id}", ctx)
    assert "Deleted fact" in capsys.readouterr().out

    await dispatch_command("/project memory alpha", ctx)
    assert "No memory recorded yet" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_memory_edit(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    await dispatch_command("/project memory alpha add original text", ctx)
    fact_id = capsys.readouterr().out.strip().split("id=")[1].split(")")[0]

    await dispatch_command(f"/project memory alpha edit {fact_id} revised text", ctx)

    assert "revised text" in capsys.readouterr().out
    await dispatch_command("/project memory alpha", ctx)
    assert "revised text" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_tools_list_shows_available_candidates(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project tools alpha", ctx)

    assert "Enabled for 'alpha': (none)" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_tools_unknown_slug_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project tools nope", ctx)
    assert "No project named 'nope'" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_project_tools_enable_and_disable_round_trip(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create alpha", ctx)
    capsys.readouterr()

    await dispatch_command("/project tools alpha enable mcp_example_*", ctx)
    assert "Enabled 'mcp_example_*'" in capsys.readouterr().out
    status = await service.get_project_status(ctx)
    assert status.projects[0].enabled_tools == ["mcp_example_*"]

    await dispatch_command("/project tools alpha disable mcp_example_*", ctx)
    assert "Disabled 'mcp_example_*'" in capsys.readouterr().out
    status = await service.get_project_status(ctx)
    assert status.projects[0].enabled_tools == []


# --- /mcp --------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_reports_no_servers_when_none_connected(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/mcp", ctx)
    assert "No MCP servers connected" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_mcp_lists_connected_servers_with_tool_counts(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    ctx.registry.register(
        ToolSpec(name="mcp_example_reverse_text", description="x", input_schema={"type": "object", "properties": {}}),
        _dummy_handler,
    )
    ctx.registry.register(
        ToolSpec(name="mcp_example_word_count", description="x", input_schema={"type": "object", "properties": {}}),
        _dummy_handler,
    )
    ctx.mcp_manager.connected_servers.append("example")

    await dispatch_command("/mcp", ctx)
    assert "example -- 2 tool(s)" in capsys.readouterr().out


# --- /schedule ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_list_when_empty(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule", ctx)
    assert "No schedules yet" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_schedule_add_once(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add once 2026-10-05T15:00:00 remind me to renew insurance", ctx)
    out = capsys.readouterr().out
    assert "Scheduled" in out
    assert "2026-10-05 15:00" in out

    schedules = await ctx.schedule_store.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].task == "remind me to renew insurance"
    assert schedules[0].trigger_type == "once"
    assert schedules[0].project_slug is None

@pytest.mark.asyncio
async def test_schedule_add_cron(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add cron 0 9 * * * daily OpenAI insight", ctx)
    out = capsys.readouterr().out
    assert "Scheduled" in out
    assert "每天 09:00" in out

    schedules = await ctx.schedule_store.list_schedules()
    assert schedules[0].cron_expression == "0 9 * * *"
    assert schedules[0].task == "daily OpenAI insight"

@pytest.mark.asyncio
async def test_schedule_add_cron_with_project(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create ai_governance", ctx)
    capsys.readouterr()

    await dispatch_command("/schedule add cron 0 9 * * * project ai_governance daily insight", ctx)

    schedules = await ctx.schedule_store.list_schedules()
    assert schedules[0].project_slug == "ai_governance"
    assert schedules[0].task == "daily insight"

@pytest.mark.asyncio
async def test_schedule_add_cron_unknown_project_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add cron 0 9 * * * project ghost some task", ctx)
    assert "No project named 'ghost'" in capsys.readouterr().out
    assert await ctx.schedule_store.list_schedules() == []

@pytest.mark.asyncio
async def test_schedule_add_cron_too_few_tokens_shows_usage(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add cron 0 9 * *", ctx)  # only 4 cron fields, no task text at all
    assert "Usage: /schedule" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_schedule_add_cron_invalid_expression_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add cron 0 9 * * not enough fields", ctx)
    assert "Invalid cron expression" in capsys.readouterr().out
    assert await ctx.schedule_store.list_schedules() == []

@pytest.mark.asyncio
async def test_schedule_list_shows_created_schedules(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule add cron 0 9 * * * daily insight", ctx)
    capsys.readouterr()

    await dispatch_command("/schedule", ctx)
    out = capsys.readouterr().out
    assert "daily insight" in out
    assert "(unaffiliated)" in out

@pytest.mark.asyncio
async def test_schedule_pause_and_resume(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
    capsys.readouterr()

    await dispatch_command(f"/schedule pause {schedule.id}", ctx)
    assert f"Paused '{schedule.id}'" in capsys.readouterr().out
    assert (await ctx.schedule_store.get_schedule(schedule.id)).enabled is False

    await dispatch_command(f"/schedule resume {schedule.id}", ctx)
    assert f"Resumed '{schedule.id}'" in capsys.readouterr().out
    assert (await ctx.schedule_store.get_schedule(schedule.id)).enabled is True

@pytest.mark.asyncio
async def test_schedule_pause_unknown_id_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/schedule pause nonexistent", ctx)
    assert "No such schedule" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_schedule_delete(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
    capsys.readouterr()

    await dispatch_command(f"/schedule delete {schedule.id}", ctx)

    assert f"Deleted '{schedule.id}'" in capsys.readouterr().out
    assert await ctx.schedule_store.get_schedule(schedule.id) is None

@pytest.mark.asyncio
async def test_schedule_edit_task(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    schedule = await ctx.schedule_store.create_schedule(task="old", trigger_type="recurring", cron_expression="0 9 * * *")
    capsys.readouterr()

    await dispatch_command(f"/schedule edit {schedule.id} task new text here", ctx)

    assert f"Updated '{schedule.id}'" in capsys.readouterr().out
    assert (await ctx.schedule_store.get_schedule(schedule.id)).task == "new text here"

@pytest.mark.asyncio
async def test_schedule_edit_cron(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    run_at = "2026-10-05T15:00:00"
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="once", run_at=run_at)
    capsys.readouterr()

    await dispatch_command(f"/schedule edit {schedule.id} cron 0 9 * * *", ctx)

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert updated.trigger_type == "recurring"
    assert updated.cron_expression == "0 9 * * *"

@pytest.mark.asyncio
async def test_schedule_edit_project_to_none(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/project create ai_governance", ctx)
    schedule = await ctx.schedule_store.create_schedule(
        task="x", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="ai_governance"
    )
    capsys.readouterr()

    await dispatch_command(f"/schedule edit {schedule.id} project none", ctx)

    assert (await ctx.schedule_store.get_schedule(schedule.id)).project_slug is None

@pytest.mark.asyncio
async def test_schedule_edit_unknown_project_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
    capsys.readouterr()

    await dispatch_command(f"/schedule edit {schedule.id} project ghost", ctx)

    assert "No project named 'ghost'" in capsys.readouterr().out

# --- /device (N14 -- Auralis / remote access) -------------------------------

@pytest.mark.asyncio
async def test_device_list_when_empty(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/device", ctx)
    assert "No devices registered yet" in capsys.readouterr().out

@pytest.mark.asyncio
async def test_device_register_prints_a_one_time_token(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)

    await dispatch_command("/device register test-phone", ctx)

    out = capsys.readouterr().out
    assert "Registered device 'test-phone'" in out
    assert "Token (shown once" in out
    devices = await ctx.device_store.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "test-phone"

@pytest.mark.asyncio
async def test_device_list_shows_registered_devices(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    await dispatch_command("/device register test-phone", ctx)
    capsys.readouterr()

    await dispatch_command("/device", ctx)

    out = capsys.readouterr().out
    assert "test-phone" in out
    assert "never" in out  # last_seen_at, before any token verification

@pytest.mark.asyncio
async def test_device_revoke(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)
    device, _ = await ctx.device_store.create_device("test-phone")
    capsys.readouterr()

    await dispatch_command(f"/device revoke {device.id}", ctx)

    assert f"Revoked '{device.id}'" in capsys.readouterr().out
    assert await ctx.device_store.list_devices() == []

@pytest.mark.asyncio
async def test_device_revoke_unknown_id_reports_error(tmp_path, capsys):
    ctx = _build_ctx(tmp_path)

    await dispatch_command("/device revoke nonexistent", ctx)

    assert "No such device" in capsys.readouterr().out
