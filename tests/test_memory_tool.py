"""Tests for the remember_fact/recall_facts tools at the ToolRegistry
dispatch layer. Mirrors tests/test_notes_tool.py's style.
"""
from __future__ import annotations

import pytest

from tools.memory.memory_store import MemoryStore
from tools.memory.memory_tool import register_memory_tools
from tools.registry import ToolRegistry


@pytest.fixture
def registry(tmp_path) -> ToolRegistry:
    registry = ToolRegistry()
    store = MemoryStore(tmp_path / "facts.json")
    register_memory_tools(registry, store)
    return registry


@pytest.mark.asyncio
async def test_remember_then_recall_round_trip(registry):
    remember_result = await registry.dispatch("remember_fact", {"content": "Prefers tea over coffee"})
    assert "Remembered" in remember_result
    assert "Prefers tea over coffee" in remember_result

    recall_result = await registry.dispatch("recall_facts", {"query": "tea"})
    assert "Prefers tea over coffee" in recall_result


@pytest.mark.asyncio
async def test_recall_no_match_returns_friendly_text_not_error(registry):
    result = await registry.dispatch("recall_facts", {"query": "nonexistent"})
    assert result == "No matching facts found in memory."


@pytest.mark.asyncio
async def test_recall_without_query_returns_everything(registry):
    await registry.dispatch("remember_fact", {"content": "fact one"})
    await registry.dispatch("remember_fact", {"content": "fact two"})

    result = await registry.dispatch("recall_facts", {})

    assert "fact one" in result and "fact two" in result
