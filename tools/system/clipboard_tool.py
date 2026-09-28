"""Clipboard read/write (Epic L2) — the first of tools/system/'s four
system/desktop-control modules, all owned by orchestrator directly (same
"local resource management" category as tools/files/), not delegated to
a worker.

pyperclip is a thin, dependency-light cross-platform wrapper: it shells
out to pbcopy/pbpaste on macOS, clip.exe/PowerShell on Windows, and
xclip/xsel/wl-clipboard on Linux, rather than this project reimplementing
per-OS clipboard access itself.

Neither tool is confirmation-gated:
  - write_clipboard overwrites whatever the user currently has copied,
    but that's reversible and low-stakes -- the same "not gated" precedent
    write_file(mode="overwrite")/update_note(mode="overwrite") already
    established (see tools/files/file_tool.py's module docstring).
  - read_clipboard is a plain read, matching read_file's own ungated
    precedent -- but unlike a file, the clipboard is ambient system state
    the user didn't necessarily intend to share with this conversation.
    Whatever's on it (which could be a password, a token, anything the
    user happened to copy moments ago for an unrelated reason) flows
    straight into the LLM's context AND logs/session-*.jsonl. This is a
    real privacy consideration, deliberately not hidden here -- same
    "state the caveat plainly rather than pretend it doesn't exist"
    posture as fetch_url's documented lack of SSRF filtering.
"""
from __future__ import annotations

from typing import Any

import pyperclip

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def register_clipboard_tools(registry: ToolRegistry, max_chars: int) -> None:
    async def read_clipboard(args: dict[str, Any]) -> str:
        try:
            content = pyperclip.paste()
        except pyperclip.PyperclipException as exc:
            raise ToolExecutionError(f"Could not access the system clipboard: {exc}") from exc

        if not content:
            return "(clipboard is empty)"
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... (truncated, {len(content)} chars total)"
        return content

    async def write_clipboard(args: dict[str, Any]) -> str:
        text = args["text"]
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException as exc:
            raise ToolExecutionError(f"Could not access the system clipboard: {exc}") from exc
        return f"Copied {len(text)} character(s) to the clipboard."

    registry.register(
        ToolSpec(
            name="read_clipboard",
            description=(
                "Read the current text content of the system clipboard. Note: this returns whatever the "
                "user most recently copied, which may be unrelated to this conversation and could be "
                "sensitive (a password, a token, etc.) -- only use this when the user's request genuinely "
                "implies they want you to look at what they copied."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        read_clipboard,
    )
    registry.register(
        ToolSpec(
            name="write_clipboard",
            description="Replace the system clipboard's content with the given text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to copy to the clipboard."}},
                "required": ["text"],
            },
        ),
        write_clipboard,
    )
