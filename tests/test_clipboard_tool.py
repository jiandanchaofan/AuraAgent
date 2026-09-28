"""Tests for tools/system/clipboard_tool.py. Runs against the REAL system
clipboard on this machine (no mocking) -- saves and restores whatever was
on it before the test, so running the suite never clobbers the
developer's own clipboard content.
"""
from __future__ import annotations

import pyperclip
import pytest

from tools.registry import ToolRegistry
from tools.system.clipboard_tool import register_clipboard_tools


@pytest.fixture(autouse=True)
def _restore_clipboard():
    original = pyperclip.paste()
    yield
    pyperclip.copy(original)


def _registry(max_chars: int = 20_000) -> ToolRegistry:
    registry = ToolRegistry()
    register_clipboard_tools(registry, max_chars)
    return registry


@pytest.mark.asyncio
async def test_write_then_read_round_trips_for_real():
    registry = _registry()

    text = "hello from AuraAgent"
    result = await registry.dispatch("write_clipboard", {"text": text})
    assert text not in result  # confirmation message, not an echo of secret content by design
    assert str(len(text)) in result  # character count

    read_back = await registry.dispatch("read_clipboard", {})
    assert read_back == text


@pytest.mark.asyncio
async def test_read_empty_clipboard_reports_clearly():
    registry = _registry()
    pyperclip.copy("")

    result = await registry.dispatch("read_clipboard", {})

    assert result == "(clipboard is empty)"


@pytest.mark.asyncio
async def test_read_clipboard_truncates_long_content():
    registry = _registry(max_chars=10)
    pyperclip.copy("x" * 100)

    result = await registry.dispatch("read_clipboard", {})

    assert result.startswith("x" * 10)
    assert "truncated" in result
    assert "100" in result
