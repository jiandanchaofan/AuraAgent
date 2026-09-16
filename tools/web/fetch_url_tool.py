"""fetch_url — fetch a web page (http/https only) and return readable text.

Uses httpx.AsyncClient (async-native, fits the project's asyncio
architecture) with a scheme allowlist, timeout, and a streamed size cap so
a single tool call can't hang forever or blow up the LLM's context window
with an enormous or infinite response.

Known limitation, not addressed in this pass: no SSRF hardening against
private/internal IP ranges or cloud metadata endpoints (e.g.
169.254.169.254) post-DNS-resolution — only the URL scheme is validated.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.web.html_to_text import html_to_text

_ALLOWED_SCHEMES = {"http", "https"}

# Some sites' bot-detection/WAF layers (observed against a real Hostinger
# "hcdn"-fronted site) return 403 for httpx's bare default request even
# though the page is a normal public page reachable in any browser or via
# curl — almost certainly fingerprinting request-header presence/order
# rather than anything httpx does wrong. Sending the same baseline headers
# curl/browsers send by default (nothing deceptive, just not omitting
# them) is enough to pass that check. This does NOT help against sites
# using a JS-execution challenge (e.g. a "checking your browser" meta-
# refresh interstitial) — that requires an actual browser engine, which
# this project deliberately does not depend on.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def build_default_http_client() -> httpx.AsyncClient:
    """Real-network client for main.py's composition root. Tests should
    keep constructing their own httpx.AsyncClient(transport=MockTransport(...))
    directly rather than using this — it's only about looking like an
    ordinary browser request on the wire, not about test behavior."""
    return httpx.AsyncClient(headers=DEFAULT_HEADERS)


def register_fetch_url_tools(
    registry: ToolRegistry,
    http_client: httpx.AsyncClient,
    timeout_seconds: float,
    max_bytes: int,
) -> None:
    async def fetch_url(args: dict[str, Any]) -> str:
        url = args["url"]
        scheme = urlsplit(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ToolExecutionError(f"Unsupported URL scheme '{scheme}'. Only http/https are allowed.")

        try:
            async with http_client.stream("GET", url, timeout=timeout_seconds, follow_redirects=True) as response:
                if response.status_code >= 400:
                    raise ToolExecutionError(f"Request failed with status {response.status_code}.")
                content_type = response.headers.get("content-type", "")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        break
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(f"Request to {url} timed out.") from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"Request to {url} failed: {exc}") from exc

        truncated = len(body) > max_bytes
        text_bytes = bytes(body[:max_bytes])

        if "html" in content_type:
            text = html_to_text(text_bytes.decode("utf-8", errors="replace"))
        elif "text" in content_type or "json" in content_type:
            text = text_bytes.decode("utf-8", errors="replace")
        else:
            return f"Cannot display content of type '{content_type or 'unknown'}' as text."

        if truncated:
            text += "\n\n[Content truncated due to size limit.]"
        return text or "(empty response body)"

    registry.register(
        ToolSpec(
            name="fetch_url",
            description="Fetch a web page (http/https only) and return its readable text content.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Absolute http or https URL."}},
                "required": ["url"],
            },
        ),
        fetch_url,
    )
