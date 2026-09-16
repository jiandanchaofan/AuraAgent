"""Unit tests for MemoryStore's persistence + search, independent of the
memory_tool/ToolRegistry layer (see test_memory_tool.py). Mirrors
tests/test_local_json_calendar.py's style.
"""
from __future__ import annotations

from tools.memory.memory_store import MemoryStore


def test_add_and_search_round_trip(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")

    fact = store.add_fact("User prefers dark mode")
    results = store.search_facts("dark mode")

    assert len(results) == 1
    assert results[0].id == fact.id
    assert results[0].content == "User prefers dark mode"


def test_search_is_case_insensitive(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    store.add_fact("Lives in Paris")

    assert len(store.search_facts("PARIS")) == 1
    assert len(store.search_facts("paris")) == 1


def test_empty_query_returns_all_facts(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    store.add_fact("fact one")
    store.add_fact("fact two")

    assert len(store.search_facts("")) == 2


def test_no_match_returns_empty_list_not_error(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    store.add_fact("fact one")

    assert store.search_facts("nonexistent") == []


def test_data_persists_across_store_instances(tmp_path):
    file_path = tmp_path / "facts.json"
    fact = MemoryStore(file_path).add_fact("Persisted fact")

    reloaded = MemoryStore(file_path)
    results = reloaded.search_facts("Persisted")
    assert len(results) == 1
    assert results[0].id == fact.id
