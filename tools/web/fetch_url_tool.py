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

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox
from tools.web.html_to_text import html_to_text

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

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


def register_http_tools(
    registry: ToolRegistry,
    http_client: httpx.AsyncClient,
    timeout_seconds: float,
    max_bytes: int,
    workspace_root: Path,
    download_max_bytes: int,
) -> None:
    """http_request and download_file — more general siblings of
    fetch_url (registered separately above; fetch_url's own behavior is
    unchanged) for cases it can't cover: submitting a form/calling a JSON
    API (needs POST/PUT/etc. and custom headers), or saving an actual file
    rather than reading it as text. Same scheme allowlist/timeout
    conventions as fetch_url; download_file additionally routes its
    destination through tools/sandbox_path.resolve_within_sandbox() against
    `workspace_root` — a download is still a write to local disk, so it
    gets the exact same sandboxing tools/files/file_tool.py's writes do,
    not an unrestricted path.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)

    async def http_request(args: dict[str, Any]) -> str:
        url = args["url"]
        method = args.get("method", "GET").upper()
        if method not in _ALLOWED_METHODS:
            raise ToolExecutionError(f"Unsupported method '{method}'. Allowed: {', '.join(sorted(_ALLOWED_METHODS))}.")
        scheme = urlsplit(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ToolExecutionError(f"Unsupported URL scheme '{scheme}'. Only http/https are allowed.")

        try:
            response = await http_client.request(
                method,
                url,
                headers=args.get("headers"),
                content=args.get("body"),
                timeout=timeout_seconds,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(f"Request to {url} timed out.") from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"Request to {url} failed: {exc}") from exc

        body_bytes = response.content[:max_bytes]
        truncated = len(response.content) > max_bytes
        text = body_bytes.decode("utf-8", errors="replace")
        if truncated:
            text += "\n\n[Content truncated due to size limit.]"
        return f"Status: {response.status_code}\n\n{text or '(empty response body)'}"

    async def download_file(args: dict[str, Any]) -> str:
        url = args["url"]
        destination_rel = args["destination_path"]
        scheme = urlsplit(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ToolExecutionError(f"Unsupported URL scheme '{scheme}'. Only http/https are allowed.")

        destination = resolve_within_sandbox(workspace_root, destination_rel)
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            async with http_client.stream("GET", url, timeout=timeout_seconds, follow_redirects=True) as response:
                if response.status_code >= 400:
                    raise ToolExecutionError(f"Download failed with status {response.status_code}.")
                total = 0
                with destination.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > download_max_bytes:
                            f.close()
                            destination.unlink(missing_ok=True)
                            raise ToolExecutionError(
                                f"Download exceeded the {download_max_bytes}-byte limit and was aborted."
                            )
                        f.write(chunk)
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(f"Download from {url} timed out.") from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"Download from {url} failed: {exc}") from exc

        return f"Downloaded {total} bytes to '{destination_rel}'."

    registry.register(
        ToolSpec(
            name="http_request",
            description=(
                "Make an HTTP request (GET/POST/PUT/PATCH/DELETE, http/https only) with optional custom "
                "headers and a request body. Returns the status code and response text. Use this instead "
                "of fetch_url when you need something other than a plain GET-and-read (submitting a form, "
                "calling a JSON API, etc.)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http or https URL."},
                    "method": {"type": "string", "description": "GET, POST, PUT, PATCH, or DELETE. Defaults to GET."},
                    "headers": {"type": "object", "description": "Optional request headers."},
                    "body": {"type": "string", "description": "Optional request body (e.g. JSON text)."},
                },
                "required": ["url"],
            },
        ),
        http_request,
    )
    registry.register(
        ToolSpec(
            name="download_file",
            description=(
                "Download a file from an http/https URL and save it into the local workspace. "
                "`destination_path` is relative to the workspace, e.g. 'downloads/report.pdf'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http or https URL to download."},
                    "destination_path": {"type": "string", "description": "Where to save it, relative to the workspace."},
                },
                "required": ["url", "destination_path"],
            },
        ),
        download_file,
    )
