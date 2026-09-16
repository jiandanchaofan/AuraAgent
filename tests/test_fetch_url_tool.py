"""Tests for fetch_url — no real network calls; uses httpx.MockTransport
to fake server responses at the transport layer.
"""
from __future__ import annotations

import httpx
import pytest

from core.exceptions import ToolExecutionError
from tools.registry import ToolRegistry
from tools.web.fetch_url_tool import register_fetch_url_tools


def _registry_with_handler(handler, timeout: float = 5.0, max_bytes: int = 1_000_000) -> ToolRegistry:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ToolRegistry()
    register_fetch_url_tools(registry, client, timeout, max_bytes)
    return registry


@pytest.mark.asyncio
async def test_fetch_url_converts_html_to_text_and_strips_script_style():
    def handler(request):
        html = (
            "<html><head><style>body{color:red}</style></head>"
            "<body><p>Hello <b>World</b></p><script>evil()</script></body></html>"
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    registry = _registry_with_handler(handler)
    result = await registry.dispatch("fetch_url", {"url": "https://example.com"})

    assert "Hello" in result and "World" in result
    assert "evil" not in result
    assert "color:red" not in result


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme_without_dispatching_request():
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, text="should not get here")

    registry = _registry_with_handler(handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("fetch_url", {"url": "file:///etc/passwd"})
    assert called is False


@pytest.mark.asyncio
async def test_fetch_url_truncates_oversized_body():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="A" * 1000)

    registry = _registry_with_handler(handler, max_bytes=100)
    result = await registry.dispatch("fetch_url", {"url": "https://example.com"})

    assert "[Content truncated" in result
    assert len(result) < 1000


@pytest.mark.asyncio
async def test_fetch_url_non_2xx_status_raises_tool_execution_error():
    def handler(request):
        return httpx.Response(404, text="not found")

    registry = _registry_with_handler(handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("fetch_url", {"url": "https://example.com/missing"})


@pytest.mark.asyncio
async def test_fetch_url_timeout_raises_tool_execution_error():
    def handler(request):
        raise httpx.TimeoutException("timed out")

    registry = _registry_with_handler(handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("fetch_url", {"url": "https://example.com"})


@pytest.mark.asyncio
async def test_fetch_url_unsupported_content_type_returns_explanatory_text():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n")

    registry = _registry_with_handler(handler)
    result = await registry.dispatch("fetch_url", {"url": "https://example.com/image.png"})

    assert "image/png" in result
