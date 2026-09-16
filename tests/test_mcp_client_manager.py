"""Real integration test for MCPClientManager: spawns the actual
mcp_servers/example_server.py over stdio (no mocking) and drives it
through the full pipeline — config file -> MCPClientManager -> stdio ->
mcp_tool_adapter -> shared ToolRegistry -> dispatch(). No network
dependency: example_server.py has zero external requirements beyond the
`mcp` package already installed for AuraAgent itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_integration.mcp_client_manager import MCPClientManager
from tools.registry import ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SERVER = PROJECT_ROOT / "mcp_servers" / "example_server.py"


def _write_config(tmp_path: Path, servers: list[dict]) -> Path:
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return config_path


@pytest.mark.asyncio
async def test_connects_to_example_server_and_registers_its_tools(tmp_path):
    config_path = _write_config(
        tmp_path,
        [{"name": "example", "command": sys.executable, "args": [str(EXAMPLE_SERVER)]}],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)

    try:
        await manager.connect_all()

        assert manager.connected_servers == ["example"]
        tool_names = {s.name for s in registry.get_tool_specs()}
        assert {"mcp_example_reverse_text", "mcp_example_word_count"} <= tool_names

        reversed_text = await registry.dispatch("mcp_example_reverse_text", {"text": "hello"})
        assert reversed_text == "olleh"

        count = await registry.dispatch("mcp_example_word_count", {"text": "the quick brown fox"})
        assert count == "4"
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_empty_servers_list_is_a_fast_no_op(tmp_path):
    config_path = _write_config(tmp_path, [])
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)

    await manager.connect_all()

    assert manager.connected_servers == []
    assert registry.get_tool_specs() == []
    await manager.close_all()


@pytest.mark.asyncio
async def test_broken_server_is_skipped_without_raising(tmp_path):
    config_path = _write_config(
        tmp_path,
        [{"name": "broken", "command": sys.executable, "args": ["-c", "import sys; sys.exit(1)"]}],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)

    await manager.connect_all()  # must not raise

    assert manager.connected_servers == []
    assert registry.get_tool_specs() == []
    await manager.close_all()
