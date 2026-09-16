"""Unit tests for MemoryStore's persistence + search, independent of the
memory_tool/ToolRegistry layer (see test_memory_tool.py). Mirrors
tests/test_local_json_calendar.py's style.
"""
from __future__ import annotations

import pytest

from tools.memory.memory_store import MemoryStore


@pytest.mark.asyncio
async def test_add_and_search_round_trip(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")

    fact = await store.add_fact("User prefers dark mode")
    results = await store.search_facts("dark mode")

    assert len(results) == 1
    assert results[0].id == fact.id
    assert results[0].content == "User prefers dark mode"


@pytest.mark.asyncio
async def test_search_is_case_insensitive(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    await store.add_fact("Lives in Paris")

    assert len(await store.search_facts("PARIS")) == 1
    assert len(await store.search_facts("paris")) == 1


@pytest.mark.asyncio
async def test_empty_query_returns_all_facts(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    await store.add_fact("fact one")
    await store.add_fact("fact two")

    assert len(await store.search_facts("")) == 2


@pytest.mark.asyncio
async def test_no_match_returns_empty_list_not_error(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    await store.add_fact("fact one")

    assert await store.search_facts("nonexistent") == []


@pytest.mark.asyncio
async def test_data_persists_across_store_instances(tmp_path):
    file_path = tmp_path / "facts.json"
    fact = await MemoryStore(file_path).add_fact("Persisted fact")

    reloaded = MemoryStore(file_path)
    results = await reloaded.search_facts("Persisted")
    assert len(results) == 1
    assert results[0].id == fact.id


@pytest.mark.asyncio
async def test_concurrent_add_fact_does_not_lose_updates(tmp_path):
    import asyncio

    store = MemoryStore(tmp_path / "facts.json")

    await asyncio.gather(*(store.add_fact(f"fact {i}") for i in range(20)))

    results = await store.search_facts("")
    assert len(results) == 20
    assert len({f.id for f in results}) == 20
