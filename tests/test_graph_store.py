"""Tests for tools/personal_graph/graph_store.py -- idempotency, the
human-authority enforcement rules (the part most worth getting right:
getting this wrong means silently overwriting a user's own data), and
the change_log cursor used by GET /api/sync/changes.
"""
from __future__ import annotations

import pytest

from tools.personal_graph.graph_store import GraphStore


def _store(tmp_path) -> GraphStore:
    return GraphStore(tmp_path / "graph.db")


@pytest.mark.asyncio
async def test_apply_op_creates_a_new_footprint(tmp_path):
    store = _store(tmp_path)

    result = await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "hello"}, source="user")

    assert result == {"status": "ok", "version": 0}
    footprints = await store.list_footprints()
    assert footprints[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_apply_op_updates_and_bumps_version(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "hello"}, source="user")

    result = await store.apply_op("op2", "footprint", "fp1", "upsert", {"text": "hello world"}, source="user")

    assert result == {"status": "ok", "version": 1}
    footprints = await store.list_footprints()
    assert footprints[0]["text"] == "hello world"


@pytest.mark.asyncio
async def test_apply_op_is_idempotent_on_replayed_op_id(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "hello"}, source="user")

    result = await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "DIFFERENT"}, source="user")

    assert result["status"] == "duplicate"
    footprints = await store.list_footprints()
    assert footprints[0]["text"] == "hello"  # not overwritten by the replayed op


@pytest.mark.asyncio
async def test_apply_op_delete_soft_deletes(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "hello"}, source="user")

    await store.apply_op("op2", "footprint", "fp1", "delete", {}, source="user")

    assert await store.list_footprints() == []


# --- human authority: type_source priority (Auralis Readme.md 3.1) ---------


@pytest.mark.asyncio
async def test_ai_cannot_override_manual_type_source(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x", "type": "excerpt", "type_source": "manual"}, source="user")

    result = await store.apply_op(
        "op2", "footprint", "fp1", "upsert", {"type": "idea", "type_source": "ai"}, source="ai"
    )

    assert result["status"] == "skipped"
    footprints = await store.list_footprints()
    assert footprints[0]["type"] == "excerpt"  # untouched


@pytest.mark.asyncio
async def test_ai_cannot_override_tag_type_source(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x", "type": "excerpt", "type_source": "tag"}, source="user")

    result = await store.apply_op("op2", "footprint", "fp1", "upsert", {"type": "idea", "type_source": "ai"}, source="ai")

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_ai_can_override_heuristic_type_source(tmp_path):
    store = _store(tmp_path)
    await store.apply_op(
        "op1", "footprint", "fp1", "upsert", {"text": "x", "type": "interpersonal", "type_source": "heuristic"}, source="user"
    )

    result = await store.apply_op("op2", "footprint", "fp1", "upsert", {"type": "idea", "type_source": "ai"}, source="ai")

    assert result["status"] == "ok"
    footprints = await store.list_footprints()
    assert footprints[0]["type"] == "idea"


@pytest.mark.asyncio
async def test_ai_can_override_default_type_source(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x", "type": "idea", "type_source": "default"}, source="user")

    result = await store.apply_op("op2", "footprint", "fp1", "upsert", {"type": "excerpt", "type_source": "ai"}, source="ai")

    assert result["status"] == "ok"


# --- human authority: link conflicts ----------------------------------------


@pytest.mark.asyncio
async def test_ai_link_discarded_when_user_link_already_exists(tmp_path):
    store = _store(tmp_path)
    await store.apply_op(
        "op1", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "user"},
        source="user",
    )

    result = await store.apply_op(
        "op2", "link", "link2", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "ai", "confidence": 0.9},
        source="ai",
    )

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_ai_link_discarded_when_dismissed_link_exists(tmp_path):
    store = _store(tmp_path)
    await store.apply_op(
        "op1", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "ai", "dismissed": 1},
        source="ai",
    )

    result = await store.apply_op(
        "op2", "link", "link2", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "ai", "confidence": 0.5},
        source="ai",
    )

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_user_link_is_never_blocked_by_an_existing_ai_link(tmp_path):
    store = _store(tmp_path)
    await store.apply_op(
        "op1", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "ai"},
        source="ai",
    )

    result = await store.apply_op(
        "op2", "link", "link2", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "user"},
        source="user",
    )

    assert result["status"] == "ok"  # the authority check only gates AI-sourced writes


# --- change_log / cursor ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_changes_since_none_returns_everything(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")
    await store.apply_op("op2", "footprint", "fp2", "upsert", {"text": "b"}, source="user")

    changes, latest_seq = await store.list_changes_since(None)

    assert [c["entity_id"] for c in changes] == ["fp1", "fp2"]
    assert latest_seq == 2


@pytest.mark.asyncio
async def test_list_changes_since_a_seq_excludes_earlier_ones(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")
    await store.apply_op("op2", "footprint", "fp2", "upsert", {"text": "b"}, source="user")

    changes, latest_seq = await store.list_changes_since(1)

    assert [c["entity_id"] for c in changes] == ["fp2"]
    assert latest_seq == 2


@pytest.mark.asyncio
async def test_duplicate_op_does_not_add_a_change_log_entry(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")

    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")

    changes, _ = await store.list_changes_since(None)
    assert len(changes) == 1


@pytest.mark.asyncio
async def test_get_latest_seq(tmp_path):
    store = _store(tmp_path)
    assert await store.get_latest_seq() == 0

    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")

    assert await store.get_latest_seq() == 1


# --- read queries ------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_person_matches_name_and_alias(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "person", "p1", "upsert", {"name": "Xiaoming", "aliases": ["小明", "XM"]}, source="user")

    by_name = await store.find_person("Xiaoming")
    by_alias = await store.find_person("小明")

    assert by_name[0]["id"] == "p1"
    assert by_alias[0]["id"] == "p1"
    assert by_alias[0]["aliases"] == ["小明", "XM"]


@pytest.mark.asyncio
async def test_list_footprints_filters_by_person_and_project(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")
    await store.apply_op("op2", "project", "proj1", "upsert", {"tag": "fitness"}, source="user")
    await store.apply_op("op3", "footprint", "fp1", "upsert", {"text": "ran with Xiaoming", "occurred_at": "2026-01-01"}, source="user")
    await store.apply_op("op4", "footprint", "fp2", "upsert", {"text": "unrelated", "occurred_at": "2026-01-02"}, source="user")
    await store.apply_op(
        "op5", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "user"},
        source="user",
    )
    await store.apply_op(
        "op6", "link", "link2", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "project", "to_id": "proj1", "source": "user"},
        source="user",
    )

    by_person = await store.list_footprints(person_id="p1")
    by_project = await store.list_footprints(project_tag="fitness")
    by_keyword = await store.list_footprints(keyword="unrelated")

    assert [f["id"] for f in by_person] == ["fp1"]
    assert [f["id"] for f in by_project] == ["fp1"]
    assert [f["id"] for f in by_keyword] == ["fp2"]


@pytest.mark.asyncio
async def test_get_project_returns_none_when_missing(tmp_path):
    store = _store(tmp_path)
    assert await store.get_project("nonexistent") is None


@pytest.mark.asyncio
async def test_get_footprint_returns_none_for_deleted_or_missing_id(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")

    assert (await store.get_footprint("fp1"))["text"] == "a"
    assert await store.get_footprint("nonexistent") is None

    await store.apply_op("op2", "footprint", "fp1", "delete", {}, source="user")
    assert await store.get_footprint("fp1") is None


@pytest.mark.asyncio
async def test_list_footprints_offset_paginates_past_the_first_page(tmp_path):
    store = _store(tmp_path)
    for i in range(3):
        await store.apply_op(f"op{i}", "footprint", f"fp{i}", "upsert", {"text": f"t{i}", "occurred_at": f"2026-01-0{i+1}"}, source="user")

    page1 = await store.list_footprints(limit=2, offset=0)
    page2 = await store.list_footprints(limit=2, offset=2)

    assert [f["id"] for f in page1] == ["fp2", "fp1"]
    assert [f["id"] for f in page2] == ["fp0"]


# --- list_persons / list_projects -------------------------------------------


@pytest.mark.asyncio
async def test_list_persons_excludes_deleted_and_filters_by_keyword(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")
    await store.apply_op("op2", "person", "p2", "upsert", {"name": "Laoli"}, source="user")
    await store.apply_op("op3", "person", "p2", "delete", {}, source="user")

    all_persons = await store.list_persons()
    filtered = await store.list_persons(keyword="Xiao")

    assert [p["id"] for p in all_persons] == ["p1"]
    assert [p["id"] for p in filtered] == ["p1"]


@pytest.mark.asyncio
async def test_list_projects_excludes_deleted_and_filters_by_keyword(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "project", "proj1", "upsert", {"tag": "fitness"}, source="user")
    await store.apply_op("op2", "project", "proj2", "upsert", {"tag": "reading"}, source="user")
    await store.apply_op("op3", "project", "proj2", "delete", {}, source="user")

    all_projects = await store.list_projects()
    filtered = await store.list_projects(keyword="fit")

    assert [p["id"] for p in all_projects] == ["proj1"]
    assert [p["id"] for p in filtered] == ["proj1"]


# --- get_footprint_links ------------------------------------------------------


@pytest.mark.asyncio
async def test_get_footprint_links_excludes_dismissed_and_soft_deleted_targets(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "x"}, source="user")
    await store.apply_op("op2", "person", "p1", "upsert", {"name": "Xiaoming"}, source="user")
    await store.apply_op("op3", "person", "p2", "upsert", {"name": "Laoli"}, source="user")
    await store.apply_op("op4", "project", "proj1", "upsert", {"tag": "fitness"}, source="user")
    await store.apply_op(
        "op5", "link", "link1", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p1", "source": "user"},
        source="user",
    )
    # Dismissed link -- excluded.
    await store.apply_op(
        "op6", "link", "link2", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "person", "to_id": "p2", "source": "ai", "dismissed": 1},
        source="ai",
    )
    # Linked project, then soft-deleted -- excluded.
    await store.apply_op(
        "op7", "link", "link3", "upsert",
        {"from_entity": "footprint", "from_id": "fp1", "to_entity": "project", "to_id": "proj1", "source": "user"},
        source="user",
    )
    await store.apply_op("op8", "project", "proj1", "delete", {}, source="user")

    links = await store.get_footprint_links("fp1")

    assert [p["id"] for p in links["persons"]] == ["p1"]
    assert links["projects"] == []


# --- list_trash / restore_entity ----------------------------------------------


@pytest.mark.asyncio
async def test_list_trash_returns_only_soft_deleted_rows_newest_first(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")
    await store.apply_op("op2", "footprint", "fp2", "upsert", {"text": "b"}, source="user")
    await store.apply_op("op3", "footprint", "fp1", "delete", {}, source="user")

    trashed = await store.list_trash("footprint")

    assert [f["id"] for f in trashed] == ["fp1"]


@pytest.mark.asyncio
async def test_list_trash_rejects_unknown_entity(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        await store.list_trash("link")


@pytest.mark.asyncio
async def test_restore_entity_undeletes_and_bumps_version(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")
    await store.apply_op("op2", "footprint", "fp1", "delete", {}, source="user")

    result = await store.restore_entity("op3", "footprint", "fp1")

    assert result == {"status": "ok", "version": 2}
    footprints = await store.list_footprints()
    assert footprints[0]["id"] == "fp1"


@pytest.mark.asyncio
async def test_restore_entity_is_idempotent_on_replayed_op_id(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")
    await store.apply_op("op2", "footprint", "fp1", "delete", {}, source="user")
    await store.restore_entity("op3", "footprint", "fp1")

    result = await store.restore_entity("op3", "footprint", "fp1")

    assert result == {"status": "duplicate", "version": None}


@pytest.mark.asyncio
async def test_restore_entity_returns_not_found_for_unknown_id(tmp_path):
    store = _store(tmp_path)

    result = await store.restore_entity("op1", "footprint", "nonexistent")

    assert result == {"status": "not_found", "version": None}


@pytest.mark.asyncio
async def test_restore_entity_returns_not_deleted_when_not_soft_deleted(tmp_path):
    store = _store(tmp_path)
    await store.apply_op("op1", "footprint", "fp1", "upsert", {"text": "a"}, source="user")

    result = await store.restore_entity("op2", "footprint", "fp1")

    assert result == {"status": "not_deleted", "version": 0}
