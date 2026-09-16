"""Adapts one MCP server's advertised tool into AuraAgent's ToolSpec /
ToolRegistry.register() shape, so MCP-provided tools become
indistinguishable from native tools once registered — the engine
dispatches them through the exact same ToolRegistry.dispatch() call path.

Tool names are prefixed with `mcp_<server_name>_` to avoid collisions with
native tools and with other MCP servers' tools that might happen to share
a tool name — the same namespacing convention mainstream MCP clients
(e.g. Claude Desktop) use.
"""
from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.types import Tool as MCPTool

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def adapt_and_register(
    mcp_tool: MCPTool,
    session: ClientSession,
    registry: ToolRegistry,
    server_name: str,
) -> None:
    qualified_name = f"mcp_{server_name}_{mcp_tool.name}"

    async def handler(args: dict[str, Any]) -> str:
        result = await session.call_tool(mcp_tool.name, args)
        text_parts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        text = "\n".join(text_parts) if text_parts else "(tool returned no text content)"
        if result.is_error:
            raise ToolExecutionError(text)
        return text

    registry.register(
        ToolSpec(
            name=qualified_name,
            description=mcp_tool.description or f"MCP tool '{mcp_tool.name}' from server '{server_name}'",
            input_schema=mcp_tool.input_schema,
        ),
        handler,
    )
