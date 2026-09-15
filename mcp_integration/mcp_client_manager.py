"""MCPClientManager — reads config/mcp_servers.json and connects to
configured MCP servers (stdio transport first; SSE as a stretch goal),
registering each server's advertised tools into the shared ToolRegistry
via mcp_tool_adapter.adapt_and_register().

v1 status: this is a real seam, not just a description. connect_all() can
already be called unconditionally against the shipped
config/mcp_servers.json (a valid but empty {"servers": []}) — with no
servers configured it's a fast no-op. The moment a server entry is added,
no code changes are needed for a connection attempt to be made; only the
body below needs implementing.

TODO (next iteration): implement connect_all()/close_all() using the
`mcp` package's ClientSession + stdio_client / StdioServerParameters.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.registry import ToolRegistry


class MCPClientManager:
    def __init__(self, config_path: Path, registry: ToolRegistry) -> None:
        self._config_path = config_path
        self._registry = registry
        self._sessions: list[object] = []

    async def connect_all(self) -> None:
        config = json.loads(self._config_path.read_text(encoding="utf-8"))
        servers = config.get("servers", [])
        if not servers:
            return
        raise NotImplementedError(
            "MCP server connections are not implemented yet — config/mcp_servers.json "
            "has entries but connect_all() only supports the empty-list no-op so far."
        )

    async def close_all(self) -> None:
        self._sessions.clear()
