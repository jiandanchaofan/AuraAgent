"""Adapts one MCP server's advertised tool into AuraAgent's ToolSpec /
ToolRegistry.register() shape, so MCP-provided tools become
indistinguishable from native tools once registered — the engine dispatches
them through the exact same ToolRegistry.dispatch() call path.

TODO (next iteration): implement adapt_and_register() once
MCPClientManager.connect_all() can produce live ClientSession objects to
call the MCP tool through.
"""
from __future__ import annotations

from tools.registry import ToolRegistry


def adapt_and_register(mcp_tool: object, session: object, registry: ToolRegistry) -> None:
    raise NotImplementedError("MCP tool adaptation lands alongside MCPClientManager's real implementation.")
