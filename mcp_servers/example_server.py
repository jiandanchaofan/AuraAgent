"""Minimal example MCP server — verifies MCPClientManager's stdio
connection end-to-end with zero external network dependency (unlike most
publicly documented MCP servers, which are fetched via `npx`/`npm` at
launch time). Two trivial tools, just enough to prove the whole pipeline
works: config/mcp_servers.json -> MCPClientManager -> mcp_tool_adapter ->
shared ToolRegistry -> callable by the ReAct engine like any native tool.

Run standalone for manual testing:
    python mcp_servers/example_server.py
Normally launched automatically by AuraAgent per config/mcp_servers.json.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("aura-example-server")


@server.tool()
def reverse_text(text: str) -> str:
    """Reverse the characters in a string."""
    return text[::-1]


@server.tool()
def word_count(text: str) -> int:
    """Count whitespace-separated words in a string."""
    return len(text.split())


if __name__ == "__main__":
    server.run()
