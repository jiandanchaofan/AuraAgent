"""Tests for tools/personal_graph/graph_tool.py -- the Leader-facing
tools over the personal graph: three read-only queries plus
create_footprint (the chat-typed "record a footprint" entry point,
independent from save_quick_note).
"""
from __future__ import annotations

import re

import pytest

from tools.personal_graph.graph_store import GraphStore
from tools.personal_graph.graph_tool import register_personal_graph_tools
from tools.registry import ToolRegistry

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _registry(tmp_path) -> tuple[ToolRegistry, GraphStore]:
    store = GraphStore(tmp_path / "graph.db")
    registry = ToolRegistry()
    register_personal_graph_tools(registry, store)
    return registry, store


@pytest.mark.asyncio
async def test_create_footprint_generates_a_valid_uuid7(tmp_path):
    registry, store = _registry(tmp_path)

    result = await registry.dispatch("create_footprint", {"text": "went for a run"})

    footprint_id = result.split("'")[1]
    assert _UUID_RE.match(footprint_id), footprint_id
    footprints = await store.list_footprints()
    assert footprints[0]["text"] == "went for a run"
    assert footprints[0]["type_source"] == "default"  # no type given


@pytest.mark.asyncio
async def test_create_footprint_with_explicit_type_is_manual_sourced(tmp_path):
    registry, store = _registry(tmp_path)

    await registry.dispatch("create_footprint", {"text": "deep chat", "type": "interpersonal"})

    footprints = await store.list_footprints()
    assert footprints[0]["type"] == "interpersonal"
    assert footprints[0]["type_source"] == "manual"


@pytest.mark.asyncio
async def test_create_footprint_links_to_an_existing_person(tmp_path):
    registry, store = _registry(tmp_path)
    await store.apply_op("op0", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")

    await registry.dispatch("create_footprint", {"text": "ran with Xiaoming", "person_names": ["Xiaoming"]})

    footprints = await store.list_footprints(person_id="p1")
    assert len(footprints) == 1


@pytest.mark.asyncio
async def test_create_footprint_auto_creates_an_unmatched_person(tmp_path):
    registry, store = _registry(tmp_path)

    await registry.dispatch("create_footprint", {"text": "met someone new", "person_names": ["Laoli"]})

    matches = await store.find_person("Laoli")
    assert len(matches) == 1
    footprints = await store.list_footprints(person_id=matches[0]["id"])
    assert len(footprints) == 1


@pytest.mark.asyncio
async def test_create_footprint_links_to_a_project_creating_it_if_missing(tmp_path):
    registry, store = _registry(tmp_path)

    await registry.dispatch("create_footprint", {"text": "hit a new PR", "project_tag": "fitness"})

    project = await store.get_project("fitness")
    assert project is not None
    footprints = await store.list_footprints(project_tag="fitness")
    assert len(footprints) == 1


@pytest.mark.asyncio
async def test_recall_person_reports_no_match_clearly(tmp_path):
    registry, _store = _registry(tmp_path)

    result = await registry.dispatch("recall_person", {"name_or_alias": "nobody"})

    assert "No person found" in result


@pytest.mark.asyncio
async def test_recall_footprints_filters_by_keyword(tmp_path):
    registry, store = _registry(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "bought milk", "occurred_at": "2026-01-01"}, source="user")
    await store.apply_op("op2", "footprint", "fp2", "upsert", {"text": "called mom", "occurred_at": "2026-01-02"}, source="user")

    result = await registry.dispatch("recall_footprints", {"keyword": "milk"})

    assert "bought milk" in result
    assert "called mom" not in result


@pytest.mark.asyncio
async def test_recall_project_context_reports_no_match_clearly(tmp_path):
    registry, _store = _registry(tmp_path)

    result = await registry.dispatch("recall_project_context", {"tag": "nonexistent"})

    assert "No project found" in result


@pytest.mark.asyncio
async def test_recall_project_context_includes_recent_footprints(tmp_path):
    registry, store = _registry(tmp_path)
    await registry.dispatch("create_footprint", {"text": "milestone reached", "project_tag": "fitness"})

    result = await registry.dispatch("recall_project_context", {"tag": "fitness"})

    assert "fitness" in result
    assert "milestone reached" in result
