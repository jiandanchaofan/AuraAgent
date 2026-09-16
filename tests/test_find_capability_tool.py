"""Tests for the find_capability tool at the ToolRegistry dispatch layer.
Exercises the real internal search against a real ToolRegistry/
ScopedToolRegistryView, and the two external searches via
httpx.MockTransport (see test_capability_search.py for the lower-level
capability_search.py unit tests this tool composes).
"""
from __future__ import annotations

import httpx
import pytest

from agents.scoped_tool_registry import ScopedToolRegistryView
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.find_capability_tool import register_find_capability_tool


async def _handler(args):
    return "ok"


def _spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(name=name, description=description, input_schema={"type": "object", "properties": {}})


def _empty_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "skillsmp.com" in str(request.url):
            return httpx.Response(200, json={"success": True, "data": {"skills": []}})
        return httpx.Response(200, json={"servers": []})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_find_capability_reports_internal_match_and_grant_status():
    registry = ToolRegistry()
    registry.register(_spec("market_new_products", "Research new product launches"), _handler)
    view = ScopedToolRegistryView(registry, ["calculate"])  # does not include market_new_products
    http_client = httpx.AsyncClient(transport=_empty_transport())
    register_find_capability_tool(registry, view, http_client, timeout_seconds=5.0)

    result = await registry.dispatch("find_capability", {"intent": "research new products in a market"})

    assert "market_new_products" in result
    assert "NOT yet granted" in result
    assert "propose_capability_grant" in result


@pytest.mark.asyncio
async def test_find_capability_reports_no_internal_match():
    registry = ToolRegistry()
    view = ScopedToolRegistryView(registry, ["*"])
    http_client = httpx.AsyncClient(transport=_empty_transport())
    register_find_capability_tool(registry, view, http_client, timeout_seconds=5.0)

    result = await registry.dispatch("find_capability", {"intent": "something nobody has"})

    assert "(no match)" in result


@pytest.mark.asyncio
async def test_find_capability_includes_mcp_candidate_with_install_hint():
    registry = ToolRegistry()
    view = ScopedToolRegistryView(registry, ["*"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "registry.modelcontextprotocol.io" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "servers": [
                        {
                            "server": {
                                "name": "com.example/thing",
                                "description": "does a thing",
                                "packages": [
                                    {
                                        "registryType": "npm",
                                        "identifier": "thing-mcp",
                                        "runtimeHint": "npx",
                                        "transport": {"type": "stdio"},
                                        "runtimeArguments": [{"value": "-y", "type": "positional"}],
                                        "environmentVariables": [],
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"success": True, "data": {"skills": []}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    register_find_capability_tool(registry, view, http_client, timeout_seconds=5.0)

    result = await registry.dispatch("find_capability", {"intent": "do a thing"})

    assert "com.example/thing" in result
    assert "propose_mcp_server(command='npx', args=['-y', 'thing-mcp'])" in result


@pytest.mark.asyncio
async def test_find_capability_includes_skillsmp_candidate_with_install_hint():
    registry = ToolRegistry()
    view = ScopedToolRegistryView(registry, ["*"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "skillsmp.com" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "skills": [
                            {
                                "name": "nano-pdf",
                                "author": "openclaw",
                                "description": "Edit PDFs",
                                "githubUrl": "https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf",
                                "stars": 100,
                            }
                        ]
                    },
                },
            )
        return httpx.Response(200, json={"servers": []})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    register_find_capability_tool(registry, view, http_client, timeout_seconds=5.0)

    result = await registry.dispatch("find_capability", {"intent": "edit pdfs"})

    assert "nano-pdf" in result
    assert "NOT an official source" in result
    assert "propose_external_skill(url='https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf')" in result


@pytest.mark.asyncio
async def test_find_capability_surfaces_external_search_errors_without_raising():
    registry = ToolRegistry()
    view = ScopedToolRegistryView(registry, ["*"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    register_find_capability_tool(registry, view, http_client, timeout_seconds=5.0)

    result = await registry.dispatch("find_capability", {"intent": "anything"})

    assert "search unavailable" in result
