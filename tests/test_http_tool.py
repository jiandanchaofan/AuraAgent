"""Tests for http_request/download_file (tools/web/fetch_url_tool.py's
register_http_tools) — no real network calls, httpx.MockTransport fakes
server responses at the transport layer, same style as
tests/test_fetch_url_tool.py.
"""
from __future__ import annotations

import httpx
import pytest

from core.exceptions import ToolExecutionError
from tools.registry import ToolRegistry
from tools.web.fetch_url_tool import register_http_tools


def _registry_with_handler(tmp_path, handler, timeout=5.0, max_bytes=1_000_000, download_max_bytes=1_000_000):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ToolRegistry()
    workspace = tmp_path / "workspace"
    register_http_tools(registry, client, timeout, max_bytes, workspace, download_max_bytes)
    return registry, workspace


# --- http_request --------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_request_defaults_to_get(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, text="hello")

    registry, _ = _registry_with_handler(tmp_path, handler)
    result = await registry.dispatch("http_request", {"url": "https://example.com"})

    assert "Status: 200" in result
    assert "hello" in result


@pytest.mark.asyncio
async def test_http_request_sends_post_with_body_and_headers(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers.get("x-custom") == "abc"
        assert request.content == b'{"a": 1}'
        return httpx.Response(201, text="created")

    registry, _ = _registry_with_handler(tmp_path, handler)
    result = await registry.dispatch(
        "http_request",
        {"url": "https://example.com", "method": "post", "headers": {"x-custom": "abc"}, "body": '{"a": 1}'},
    )

    assert "Status: 201" in result
    assert "created" in result


@pytest.mark.asyncio
async def test_http_request_rejects_unsupported_method(tmp_path):
    registry, _ = _registry_with_handler(tmp_path, lambda r: httpx.Response(200))
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("http_request", {"url": "https://example.com", "method": "TRACE"})


@pytest.mark.asyncio
async def test_http_request_rejects_non_http_scheme(tmp_path):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200)

    registry, _ = _registry_with_handler(tmp_path, handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("http_request", {"url": "file:///etc/passwd"})
    assert called is False


@pytest.mark.asyncio
async def test_http_request_truncates_oversized_response(tmp_path):
    registry, _ = _registry_with_handler(tmp_path, lambda r: httpx.Response(200, text="A" * 1000), max_bytes=50)
    result = await registry.dispatch("http_request", {"url": "https://example.com"})
    assert "[Content truncated" in result


@pytest.mark.asyncio
async def test_http_request_timeout_raises(tmp_path):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    registry, _ = _registry_with_handler(tmp_path, handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("http_request", {"url": "https://example.com"})


# --- download_file ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_file_saves_into_workspace(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"file bytes here")

    registry, workspace = _registry_with_handler(tmp_path, handler)
    result = await registry.dispatch("download_file", {"url": "https://example.com/f.bin", "destination_path": "downloads/f.bin"})

    assert "Downloaded" in result
    saved = workspace / "downloads" / "f.bin"
    assert saved.is_file()
    assert saved.read_bytes() == b"file bytes here"


@pytest.mark.asyncio
async def test_download_file_blocks_path_traversal(tmp_path):
    registry, workspace = _registry_with_handler(tmp_path, lambda r: httpx.Response(200, content=b"x"))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("download_file", {"url": "https://example.com/f.bin", "destination_path": "../../evil.bin"})


@pytest.mark.asyncio
async def test_download_file_rejects_non_http_scheme(tmp_path):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200)

    registry, workspace = _registry_with_handler(tmp_path, handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("download_file", {"url": "ftp://example.com/f.bin", "destination_path": "f.bin"})
    assert called is False


@pytest.mark.asyncio
async def test_download_file_aborts_and_cleans_up_when_oversized(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"X" * 1000)

    registry, workspace = _registry_with_handler(tmp_path, handler, download_max_bytes=100)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("download_file", {"url": "https://example.com/big.bin", "destination_path": "big.bin"})

    assert not (workspace / "big.bin").exists()


@pytest.mark.asyncio
async def test_download_file_non_2xx_status_raises(tmp_path):
    registry, workspace = _registry_with_handler(tmp_path, lambda r: httpx.Response(404))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("download_file", {"url": "https://example.com/missing.bin", "destination_path": "m.bin"})


@pytest.mark.asyncio
async def test_download_file_creates_parent_directories(tmp_path):
    registry, workspace = _registry_with_handler(tmp_path, lambda r: httpx.Response(200, content=b"ok"))

    await registry.dispatch("download_file", {"url": "https://example.com/f.bin", "destination_path": "a/b/c.bin"})

    assert (workspace / "a" / "b" / "c.bin").read_bytes() == b"ok"
