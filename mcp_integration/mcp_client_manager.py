"""MCPClientManager — reads config/mcp_servers.json and connects to
configured MCP servers over stdio, registering each server's advertised
tools into the shared ToolRegistry via mcp_tool_adapter.adapt_and_register().

A server that fails to start or initialize is logged and skipped rather
than aborting startup entirely — MCP servers are optional, best-effort
integrations; one misconfigured entry in config/mcp_servers.json shouldn't
prevent AuraAgent itself from starting. With an empty "servers" list this
is a fast no-op, same as before this module had a real implementation.
"""
from __future__ import annotations

import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_integration.mcp_tool_adapter import adapt_and_register
from tools.registry import ToolRegistry


class MCPClientManager:
    def __init__(self, config_path: Path, registry: ToolRegistry) -> None:
        self._config_path = config_path
        self._registry = registry
        self._exit_stack = AsyncExitStack()
        self.connected_servers: list[str] = []

    async def connect_all(self) -> None:
        config = json.loads(self._config_path.read_text(encoding="utf-8"))
        for server_config in config.get("servers", []):
            await self._connect_one(server_config)

    async def _connect_one(self, server_config: dict[str, Any]) -> None:
        server_name = server_config.get("name", "<unnamed>")
        try:
            command = server_config["command"]
            # A plain "python"/"python3" in config/mcp_servers.json means
            # "run with the same interpreter AuraAgent itself is running
            # under" — resolving it via sys.executable guarantees the child
            # process sees the same venv/site-packages (e.g. the `mcp`
            # package) rather than whatever "python" happens to resolve to
            # on PATH, which may be a different, unrelated installation.
            if command in ("python", "python3"):
                command = sys.executable
            params = StdioServerParameters(
                command=command,
                args=server_config.get("args", []),
                env=server_config.get("env"),
            )
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            for mcp_tool in tools_result.tools:
                adapt_and_register(mcp_tool, session, self._registry, server_name)

            self.connected_servers.append(server_name)
            print(f"[MCP] Connected to '{server_name}': {len(tools_result.tools)} tool(s) registered.")
        except Exception as exc:  # noqa: BLE001 - one bad server must not block startup
            print(f"[MCP] Failed to connect to '{server_name}': {type(exc).__name__}: {exc}")

    async def close_all(self) -> None:
        await self._exit_stack.aclose()
