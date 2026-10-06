"""Real integration test for MCPClientManager: spawns the actual
mcp_servers/example_server.py over stdio (no mocking) and drives it
through the full pipeline — config file -> MCPClientManager -> stdio ->
mcp_tool_adapter -> shared ToolRegistry -> dispatch(). No network
dependency: example_server.py has zero external requirements beyond the
`mcp` package already installed for AuraAgent itself.
"""
from __future__ import annotations

import asyncio
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
async def test_connect_one_from_a_different_task_than_close_all_does_not_crash(tmp_path):
    """Regression test for a real bug found during manual end-to-end
    verification of propose_mcp_server: core/react_engine.py's concurrent
    tool dispatch runs each tool call in its OWN asyncio Task (via
    asyncio.gather), so connect_one() — when reached through that tool
    call — used to run in a different, short-lived Task than the one that
    built MCPClientManager and will later call close_all(). anyio's cancel
    scopes (used internally by stdio_client/ClientSession) require entering
    and exiting from the exact same Task, so the old implementation raised
    'Attempted to exit cancel scope in a different task than it was
    entered in' at shutdown. Reproduced here by explicitly connecting from
    a spawned asyncio.Task, then calling close_all() from THIS test
    function's own (different) task."""
    registry = ToolRegistry()
    manager = MCPClientManager(tmp_path / "unused.json", registry)

    async def connect_from_another_task():
        await manager.connect_one({"name": "example", "command": sys.executable, "args": [str(EXAMPLE_SERVER)]})

    await asyncio.create_task(connect_from_another_task())

    assert manager.connected_servers == ["example"]
    await manager.close_all()  # must not raise


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


def test_tool_registry_unregister_removes_named_tools():
    from tools.base import ToolSpec

    registry = ToolRegistry()
    registry.register(ToolSpec(name="a", description="", input_schema={}), lambda args: None)
    registry.register(ToolSpec(name="b", description="", input_schema={}), lambda args: None)

    registry.unregister(["a", "nonexistent"])

    names = {s.name for s in registry.get_tool_specs()}
    assert names == {"b"}


@pytest.mark.asyncio
async def test_closing_two_independently_connected_real_servers_does_not_crash(tmp_path):
    # Regression test for a real bug found while adding per-server
    # reconnect support: giving each server its own AsyncExitStack but
    # still closing them all from one shared "owner" task raised
    # "Attempted to exit a cancel scope that isn't the current task's
    # current cancel scope" at shutdown, because anyio's cancel scopes
    # are tracked per-TASK and the two servers' scopes ended up
    # interleaved on that one task's stack in a way a simple close loop
    # couldn't unwind safely. Reproduced directly against two REAL
    # servers (not mocked) -- giving each its own dedicated Task (see
    # mcp_client_manager.py's module docstring) fixes it.
    config_path = _write_config(
        tmp_path,
        [
            {"name": "one", "command": sys.executable, "args": [str(EXAMPLE_SERVER)]},
            {"name": "two", "command": sys.executable, "args": [str(EXAMPLE_SERVER)]},
        ],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)

    await manager.connect_all()
    assert set(manager.connected_servers) == {"one", "two"}

    await manager.close_all()  # must not raise


@pytest.mark.asyncio
async def test_reconnect_server_swaps_config_and_re_registers_tools(tmp_path):
    # A synthetic server whose SKILL-style arguments include a
    # {directory} placeholder, mirroring config/mcp_servers.json's real
    # filesystem entry -- this fake server just echoes back its own argv
    # as a tool result so the test can observe which directory it was
    # actually launched with, without needing the real npx package.
    echo_server = tmp_path / "echo_server.py"
    echo_server.write_text(
        "import sys\n"
        "from mcp.server.mcpserver import MCPServer\n"
        "server = MCPServer('echo')\n"
        "ARG = sys.argv[1]\n"
        "@server.tool()\n"
        "def echo_arg() -> str:\n"
        "    return ARG\n"
        "server.run()\n",
        encoding="utf-8",
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "name": "echo",
                "command": sys.executable,
                "args": [str(echo_server), "{directory}"],
                "follow_active_directory": True,
            }
        ],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)

    await manager.connect_all(initial_directory=Path("first"))
    first_result = await registry.dispatch("mcp_echo_echo_arg", {})
    assert first_result == "first"

    await manager.reconnect_server("echo", Path("second"))
    second_result = await registry.dispatch("mcp_echo_echo_arg", {})
    assert second_result == "second"

    # Exactly one 'echo_arg' tool registered -- the old one was
    # unregistered, not left alongside the new one.
    names = [s.name for s in registry.get_tool_specs() if s.name == "mcp_echo_echo_arg"]
    assert names == ["mcp_echo_echo_arg"]

    await manager.close_all()


@pytest.mark.asyncio
async def test_reconnect_falls_back_to_a_fresh_connect_after_a_prior_failure(tmp_path):
    # Regression test for a real failure mode found during live manual
    # verification: a reconnect can fail (e.g. a transient subprocess
    # error) and clean up its own tracking -- without a fallback,
    # directory-following for that server would be permanently disabled
    # for the rest of the process's life, since nothing else ever retries
    # connecting it again. Simulated here by manually clearing the
    # bookkeeping a failed reconnect would have cleared, then calling
    # reconnect_server() again as sync_directory_following_servers() would.
    config_path = _write_config(
        tmp_path,
        [{"name": "example", "command": sys.executable, "args": [str(EXAMPLE_SERVER)], "follow_active_directory": True}],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)
    await manager.connect_all(initial_directory=Path("first"))
    assert manager.connected_servers == ["example"]

    # Simulate the cleanup a failed, isolated reconnect attempt performs
    # (see reconnect_server()'s own except branch, which also unregisters
    # the old tool names before ever attempting the new connect) -- the
    # config itself is deliberately left in place, same as the real path.
    old_names = [s.name for s in registry.get_tool_specs()]
    registry.unregister(old_names)
    manager._server_tasks.pop("example", None)
    manager._server_commands.pop("example", None)
    manager._server_directories.pop("example", None)
    manager.connected_servers.remove("example")

    await manager.reconnect_server("example", Path("second"))

    assert manager.connected_servers == ["example"]
    result = await registry.dispatch("mcp_example_word_count", {"text": "a b c"})
    assert result == "3"

    await manager.close_all()


@pytest.mark.asyncio
async def test_sync_directory_following_servers_skips_reconnect_when_directory_unchanged(tmp_path, monkeypatch):
    config_path = _write_config(
        tmp_path,
        [{"name": "example", "command": sys.executable, "args": [str(EXAMPLE_SERVER)], "follow_active_directory": True}],
    )
    registry = ToolRegistry()
    manager = MCPClientManager(config_path, registry)
    await manager.connect_all(initial_directory=Path("same"))

    calls = []
    original = manager.reconnect_server

    async def tracking_reconnect(name, directory):
        calls.append((name, directory))
        await original(name, directory)

    monkeypatch.setattr(manager, "reconnect_server", tracking_reconnect)

    await manager.sync_directory_following_servers(Path("same"))
    assert calls == []  # unchanged -- no reconnect triggered

    await manager.sync_directory_following_servers(Path("different"))
    assert calls == [("example", Path("different"))]

    await manager.close_all()


@pytest.mark.asyncio
async def test_reconnect_unknown_server_reports_clearly_without_raising(tmp_path, capsys):
    registry = ToolRegistry()
    manager = MCPClientManager(tmp_path / "unused.json", registry)

    await manager.reconnect_server("never_connected", Path("."))  # must not raise

    assert "no known config" in capsys.readouterr().out
