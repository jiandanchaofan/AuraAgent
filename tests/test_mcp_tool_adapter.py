"""Unit tests for adapt_and_register — uses a fake ClientSession-like
object, no real MCP server involved. See test_mcp_client_manager.py for
the real stdio integration test against mcp_servers/example_server.py.
"""
from __future__ import annotations

import mcp.types as types
import pytest

from core.exceptions import ToolExecutionError
from mcp_integration.mcp_tool_adapter import adapt_and_register
from tools.registry import ToolRegistry


class _FakeSession:
    def __init__(self, result: types.CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def _mcp_tool(name: str = "reverse_text", description: str | None = "Reverse text") -> types.Tool:
    return types.Tool(name=name, description=description, input_schema={"type": "object", "properties": {}})


@pytest.mark.asyncio
async def test_adapt_and_register_prefixes_name_and_dispatches_through_session():
    session = _FakeSession(
        types.CallToolResult(content=[types.TextContent(type="text", text="txet")], is_error=False)
    )
    registry = ToolRegistry()

    adapt_and_register(_mcp_tool(), session, registry, server_name="example")

    specs = {s.name for s in registry.get_tool_specs()}
    assert "mcp_example_reverse_text" in specs

    result = await registry.dispatch("mcp_example_reverse_text", {"text": "text"})
    assert result == "txet"
    # The underlying MCP tool name (unprefixed) is what gets sent over the wire.
    assert session.calls == [("reverse_text", {"text": "text"})]


@pytest.mark.asyncio
async def test_adapt_and_register_error_result_raises_tool_execution_error():
    session = _FakeSession(
        types.CallToolResult(content=[types.TextContent(type="text", text="boom")], is_error=True)
    )
    registry = ToolRegistry()

    adapt_and_register(_mcp_tool(), session, registry, server_name="example")

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("mcp_example_reverse_text", {"text": "x"})


@pytest.mark.asyncio
async def test_adapt_and_register_no_text_content_returns_placeholder():
    session = _FakeSession(types.CallToolResult(content=[], is_error=False))
    registry = ToolRegistry()

    adapt_and_register(_mcp_tool(), session, registry, server_name="example")

    result = await registry.dispatch("mcp_example_reverse_text", {"text": "x"})
    assert result == "(tool returned no text content)"


def test_adapt_and_register_uses_mcp_tool_description_and_schema():
    session = _FakeSession(types.CallToolResult(content=[], is_error=False))
    registry = ToolRegistry()

    adapt_and_register(
        types.Tool(name="reverse_text", description="does a thing", input_schema={"type": "object", "properties": {"x": {}}}),
        session,
        registry,
        server_name="srv",
    )

    spec = next(s for s in registry.get_tool_specs() if s.name == "mcp_srv_reverse_text")
    assert spec.description == "does a thing"
    assert spec.input_schema == {"type": "object", "properties": {"x": {}}}


def test_adapt_and_register_falls_back_to_generated_description_when_none():
    session = _FakeSession(types.CallToolResult(content=[], is_error=False))
    registry = ToolRegistry()

    adapt_and_register(_mcp_tool(description=None), session, registry, server_name="srv")

    spec = next(s for s in registry.get_tool_specs() if s.name == "mcp_srv_reverse_text")
    assert "reverse_text" in spec.description
    assert "srv" in spec.description
