"""Tests for tools/self_extend/capability_search.py. The internal search
runs against a real ToolRegistry/ScopedToolRegistryView; the two external
searches run against httpx.MockTransport fixtures shaped like the REAL
live responses (verified via curl against registry.modelcontextprotocol.io
and skillsmp.com before writing capability_search.py), not guessed JSON.
"""
from __future__ import annotations

import httpx
import pytest

from agents.scoped_tool_registry import ScopedToolRegistryView
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.capability_search import search_internal, search_mcp_registry, search_skillsmp

# A trimmed but structurally real MCP registry response (one stdio/npm
# package, one HTTP-only "remote" entry that must be skipped since
# MCPClientManager only implements stdio).
_MCP_REGISTRY_RESPONSE = {
    "servers": [
        {
            "server": {
                "name": "com.pulsemcp/remote-filesystem",
                "description": "MCP server for remote filesystem operations on cloud storage (GCS).",
                "repository": {"url": "https://github.com/pulsemcp/mcp-servers"},
                "packages": [
                    {
                        "registryType": "npm",
                        "identifier": "remote-filesystem-mcp-server",
                        "runtimeHint": "npx",
                        "transport": {"type": "stdio"},
                        "runtimeArguments": [{"value": "-y", "type": "positional"}],
                        "environmentVariables": [
                            {"name": "GCS_BUCKET", "isRequired": True},
                            {"name": "GCS_ROOT_PATH", "isRequired": False},
                        ],
                    }
                ],
            }
        },
        {
            "server": {
                "name": "ai.smithery/some-remote-only-server",
                "description": "Remote-only, HTTP transport.",
                "remotes": [{"type": "streamable-http", "url": "https://server.smithery.ai/x/mcp"}],
            }
        },
    ],
    "metadata": {"count": 2},
}

_SKILLSMP_RESPONSE = {
    "success": True,
    "data": {
        "skills": [
            {
                "id": "openclaw-openclaw-skills-nano-pdf-skill-md",
                "name": "nano-pdf",
                "author": "openclaw",
                "description": "Edit PDFs with natural-language instructions.",
                "githubUrl": "https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf",
                "skillUrl": "https://skillsmp.com/creators/openclaw/openclaw/skills-nano-pdf",
                "stars": 389030,
                "updatedAt": 1779015027,
            }
        ],
        "pagination": {"page": 1, "limit": 3, "total": 1, "totalPages": 1},
    },
}


async def _handler(args):
    return "ok"


def _spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(name=name, description=description, input_schema={"type": "object", "properties": {}})


# --- search_internal ---------------------------------------------------------


def test_search_internal_matches_by_keyword_in_name_or_description():
    registry = ToolRegistry()
    registry.register(_spec("market_new_products", "Research new product launches in a market segment"), _handler)
    registry.register(_spec("calculate", "Evaluate a math expression"), _handler)
    view = ScopedToolRegistryView(registry, ["*"])

    results = search_internal(registry, view, "I need to research new products in a market")

    names = {c.name for c in results}
    assert "market_new_products" in names
    assert "calculate" not in names


def test_search_internal_flags_already_allowed_vs_not():
    registry = ToolRegistry()
    registry.register(_spec("market_new_products", "market research tool"), _handler)
    view = ScopedToolRegistryView(registry, ["calculate"])  # does NOT include market_new_products

    results = search_internal(registry, view, "market research")

    assert results[0].name == "market_new_products"
    assert results[0].already_allowed is False

    view.add_allowed_pattern("market_new_products")
    results_after = search_internal(registry, view, "market research")
    assert results_after[0].already_allowed is True


def test_search_internal_excludes_self_extension_and_delegate_tools():
    registry = ToolRegistry()
    registry.register(_spec("propose_new_skill", "propose a new skill"), _handler)
    registry.register(_spec("delegate_to_researcher", "delegate a research task"), _handler)
    view = ScopedToolRegistryView(registry, ["*"])

    results = search_internal(registry, view, "skill research")

    assert results == []


def test_search_internal_returns_empty_for_no_match():
    registry = ToolRegistry()
    registry.register(_spec("calculate", "math"), _handler)
    view = ScopedToolRegistryView(registry, ["*"])

    assert search_internal(registry, view, "quantum teleportation") == []


def test_search_internal_ignores_short_words_and_empty_intent():
    registry = ToolRegistry()
    registry.register(_spec("calculate", "math"), _handler)
    view = ScopedToolRegistryView(registry, ["*"])

    assert search_internal(registry, view, "a to is") == []
    assert search_internal(registry, view, "") == []


# --- search_mcp_registry ------------------------------------------------------


@pytest.mark.asyncio
async def test_search_mcp_registry_maps_stdio_npm_package_and_skips_remote_only():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "registry.modelcontextprotocol.io" in str(request.url)
        return httpx.Response(200, json=_MCP_REGISTRY_RESPONSE)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_mcp_registry(http_client, "filesystem", timeout_seconds=5.0)

    assert error is None
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "com.pulsemcp/remote-filesystem"
    assert candidate.command == "npx"
    assert candidate.args == ["-y", "remote-filesystem-mcp-server"]
    assert candidate.env_keys_needed == ["GCS_BUCKET"]  # only the required one
    assert candidate.repository_url == "https://github.com/pulsemcp/mcp-servers"


@pytest.mark.asyncio
async def test_search_mcp_registry_handles_timeout_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_mcp_registry(http_client, "anything", timeout_seconds=1.0)

    assert candidates == []
    assert "timed out" in error.lower()


@pytest.mark.asyncio
async def test_search_mcp_registry_handles_http_error_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_mcp_registry(http_client, "anything", timeout_seconds=5.0)

    assert candidates == []
    assert error is not None


@pytest.mark.asyncio
async def test_search_mcp_registry_no_results_is_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"servers": [], "metadata": {"count": 0}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_mcp_registry(http_client, "nonexistent-xyz", timeout_seconds=5.0)

    assert candidates == []
    assert error is None


# --- search_skillsmp -----------------------------------------------------------


@pytest.mark.asyncio
async def test_search_skillsmp_maps_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "skillsmp.com" in str(request.url)
        return httpx.Response(200, json=_SKILLSMP_RESPONSE)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_skillsmp(http_client, "pdf", timeout_seconds=5.0)

    assert error is None
    assert len(candidates) == 1
    assert candidates[0].name == "nano-pdf"
    assert candidates[0].author == "openclaw"
    assert candidates[0].github_url == "https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf"
    assert candidates[0].stars == 389030


@pytest.mark.asyncio
async def test_search_skillsmp_rate_limit_returns_friendly_error_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    candidates, error = await search_skillsmp(http_client, "pdf", timeout_seconds=5.0)

    assert candidates == []
    assert "rate" in error.lower()
