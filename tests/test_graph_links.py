"""Tests for tools/personal_graph/graph_links.py -- the shared
match-or-create + link orchestration used by both
graph_tool.py::create_footprint (source="user") and gui/graph_routes.py's
REST route and async AI-enrichment task (source="user"/"ai").
"""
from __future__ import annotations

import pytest

from tools.personal_graph.graph_links import match_or_create_person, resolve_footprint_mentions
from tools.personal_graph.graph_store import GraphStore


def _store(tmp_path) -> GraphStore:
    return GraphStore(tmp_path / "graph.db")


@pytest.mark.asyncio
async def test_match_or_create_person_reuses_an_existing_match(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")

    person = await match_or_create_person(store, "Xiaoming")

    assert person["id"] == "p1"
    assert len(await store.list_persons()) == 1


@pytest.mark.asyncio
async def test_match_or_create_person_creates_a_stub_when_unmatched(tmp_path):
    store = _store(tmp_path)

    person = await match_or_create_person(store, "Laoli")

    matches = await store.find_person("Laoli")
    assert len(matches) == 1
    assert matches[0]["id"] == person["id"]


@pytest.mark.asyncio
async def test_resolve_footprint_mentions_links_both_person_and_project_and_returns_labels(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x"}, source="user")

    labels = await resolve_footprint_mentions(store, "fp1", ["Xiaoming"], "fitness", source="user")

    assert labels == ["Xiaoming", "#fitness"]
    footprints = await store.list_footprints(project_tag="fitness")
    assert [f["id"] for f in footprints] == ["fp1"]
    persons = await store.list_footprints(person_id=(await store.find_person("Xiaoming"))[0]["id"])
    assert [f["id"] for f in persons] == ["fp1"]


@pytest.mark.asyncio
async def test_resolve_footprint_mentions_with_source_ai_does_not_override_an_existing_user_link(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x"}, source="user")
    await store.apply_op("op2", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")
    await store.apply_op(
        "op3", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "user"},
        source="user",
    )

    await resolve_footprint_mentions(store, "fp1", ["Xiaoming"], None, source="ai")

    links = await store.get_footprint_links("fp1")
    assert len(links["persons"]) == 1
    assert links["persons"][0]["link_source"] == "user"  # the AI attempt was rejected, the user's own link stands
