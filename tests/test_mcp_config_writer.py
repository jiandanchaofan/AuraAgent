"""Tests for mcp_config_writer — the MCP-server counterpart to
test_agent_config_writer.py."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mcp_integration.mcp_config_writer import add_server_entry


def _write_config(tmp_path: Path, servers: list[dict]) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_add_server_entry_appends_and_persists(tmp_path):
    config_path = _write_config(tmp_path, [{"name": "example", "command": "python", "args": []}])
    lock = asyncio.Lock()

    await add_server_entry(config_path, {"name": "brave_search", "command": "npx", "args": ["-y", "brave-mcp"]}, lock)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    names = {s["name"] for s in saved["servers"]}
    assert names == {"example", "brave_search"}


@pytest.mark.asyncio
async def test_concurrent_add_server_entry_calls_do_not_clobber_each_other(tmp_path):
    config_path = _write_config(tmp_path, [])
    lock = asyncio.Lock()

    await asyncio.gather(
        add_server_entry(config_path, {"name": "server_a", "command": "python", "args": []}, lock),
        add_server_entry(config_path, {"name": "server_b", "command": "python", "args": []}, lock),
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    names = {s["name"] for s in saved["servers"]}
    assert names == {"server_a", "server_b"}
