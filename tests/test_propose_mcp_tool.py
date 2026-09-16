"""Tests for propose_mcp_server — the highest-risk self-extension tool in
AuraAgent (arbitrary subprocess, no sandbox, no code for a human to read).
Uses the repo's own harmless mcp_servers/example_server.py as the "new
server" being proposed, exactly like test_mcp_client_manager.py — real
subprocess, real stdio handshake, no mocking.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from mcp_integration.mcp_client_manager import MCPClientManager
from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.self_extend.propose_mcp_tool import register_propose_mcp_tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SERVER = PROJECT_ROOT / "mcp_servers" / "example_server.py"


def _write_mcp_config(tmp_path: Path) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"servers": []}), encoding="utf-8")
    return path


def _setup(tmp_path, decision: bool = True, answer: str = "secret-value"):
    registry = ToolRegistry()
    mcp_manager = MCPClientManager(tmp_path / "unused.json", registry)  # config_path unused by connect_one
    config_path = _write_mcp_config(tmp_path)
    lock = asyncio.Lock()
    confirmation = FakeConfirmationChannel(decision=decision, answer=answer)
    granted: list[str] = []

    async def grant_access(pattern: str) -> None:
        granted.append(pattern)

    register_propose_mcp_tool(
        registry, mcp_manager, config_path, lock, confirmation, grant_access=grant_access
    )
    return registry, mcp_manager, config_path, confirmation, granted


def _valid_args(name: str = "example2", env_keys_needed: list | None = None) -> dict:
    return {
        "name": name,
        "command": sys.executable,
        "args": [str(EXAMPLE_SERVER)],
        "env_keys_needed": env_keys_needed or [],
        "reason": "Need text-reversal tools.",
        "capability_description": "Reverses text and counts words.",
    }


@pytest.mark.asyncio
async def test_approval_connects_registers_tools_and_persists(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=True)

    try:
        result = await registry.dispatch("propose_mcp_server", _valid_args())

        assert "example2" in result
        assert "example2" in mcp_manager.connected_servers
        tool_names = {s.name for s in registry.get_tool_specs()}
        assert "mcp_example2_reverse_text" in tool_names
        assert granted == ["mcp_example2_*"]

        reversed_text = await registry.dispatch("mcp_example2_reverse_text", {"text": "hello"})
        assert reversed_text == "olleh"

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert [s["name"] for s in saved["servers"]] == ["example2"]
    finally:
        await mcp_manager.close_all()


@pytest.mark.asyncio
async def test_decline_connects_nothing_and_persists_nothing(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=False)

    result = await registry.dispatch("propose_mcp_server", _valid_args())

    assert "declined" in result.lower()
    assert mcp_manager.connected_servers == []
    assert granted == []
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["servers"] == []


@pytest.mark.asyncio
async def test_env_keys_needed_are_collected_from_the_human_not_the_llm(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(
        tmp_path, decision=True, answer="the-real-secret"
    )

    try:
        await registry.dispatch("propose_mcp_server", _valid_args(env_keys_needed=["SOME_API_KEY"]))

        assert any("SOME_API_KEY" in q for q in confirmation.questions_asked)
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["servers"][0]["env"] == {"SOME_API_KEY": "the-real-secret"}
    finally:
        await mcp_manager.close_all()


@pytest.mark.asyncio
async def test_failed_connection_is_reported_and_nothing_is_persisted(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=True)
    args = _valid_args(name="broken")
    args["command"] = sys.executable
    args["args"] = ["-c", "import sys; sys.exit(1)"]

    result = await registry.dispatch("propose_mcp_server", args)

    assert "failed" in result.lower()
    assert mcp_manager.connected_servers == []
    assert granted == []
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["servers"] == []


@pytest.mark.asyncio
async def test_invalid_name_rejected_before_bothering_the_human(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=True)

    with pytest.raises(Exception):
        await registry.dispatch("propose_mcp_server", _valid_args(name="not a valid name!"))

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_duplicate_server_name_rejected_before_bothering_the_human(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=True)
    mcp_manager.connected_servers.append("example2")  # simulate already connected

    with pytest.raises(Exception):
        await registry.dispatch("propose_mcp_server", _valid_args(name="example2"))

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_non_string_args_rejected_before_bothering_the_human(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=True)
    args = _valid_args()
    args["args"] = [1, 2, 3]

    with pytest.raises(Exception):
        await registry.dispatch("propose_mcp_server", args)

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_proposal_text_warns_about_no_sandbox_and_full_permissions(tmp_path):
    registry, mcp_manager, config_path, confirmation, granted = _setup(tmp_path, decision=False)

    await registry.dispatch("propose_mcp_server", _valid_args())

    reason = confirmation.requests[0].reason
    assert "NO SANDBOX" in reason
    assert "SAME PERMISSIONS" in reason
    assert confirmation.requests[0].risk_level == "arbitrary_execution"
