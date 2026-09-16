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
from cli.commands import dispatch_command, is_command
from cli.context import CLIContext
from core.logger import AuraLogger
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tests.fakes import FakeLLMProvider
from tools.base import ToolSpec
from tools.registry import ToolRegistry

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
    logger = AuraLogger(tmp_path / "logs")

    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    skill_loader = SkillLoader(skills_dir, registry)

    mcp_manager = MCPClientManager(tmp_path / "mcp_servers.json", registry)

    settings = SimpleNamespace(
        agents_config_path=agents_config_path,
        skills_dir=skills_dir,
        openai_base_url=None,
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
