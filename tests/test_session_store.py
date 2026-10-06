"""Tests for tools/sessions/session_store.py -- ChatSessionStore's registry
(list/create/rename/touch/delete) and its synchronous per-session event
append/read path (what gui/session_sink.py's LogSink.write() calls)."""
from __future__ import annotations

import pytest

from tools.sessions.session_store import DEFAULT_TITLE, ChatSessionStore


@pytest.fixture
def store(tmp_path):
    return ChatSessionStore(tmp_path / "chat_sessions")


async def test_create_session_defaults_to_placeholder_title(store):
    info = await store.create_session()
    assert info.title == DEFAULT_TITLE
    assert info.created_at == info.updated_at


async def test_list_sessions_sorted_most_recently_updated_first(store):
    first = await store.create_session()
    second = await store.create_session()
    await store.touch(first.id)  # bumps first back to the front

    listed = await store.list_sessions()
    assert [s.id for s in listed] == [first.id, second.id]


async def test_get_session_unknown_id_returns_none(store):
    assert await store.get_session("nonexistent") is None


async def test_pinned_sessions_sort_before_unpinned_regardless_of_recency(store):
    old_pinned = await store.create_session()
    new_unpinned = await store.create_session()
    await store.touch(new_unpinned.id)  # more recently updated than old_pinned
    await store.set_pinned(old_pinned.id, True)

    listed = await store.list_sessions()
    assert [s.id for s in listed] == [old_pinned.id, new_unpinned.id]


async def test_set_pinned_toggles_and_persists(store):
    info = await store.create_session()
    assert info.pinned is False

    pinned = await store.set_pinned(info.id, True)
    assert pinned.pinned is True
    assert (await store.get_session(info.id)).pinned is True

    unpinned = await store.set_pinned(info.id, False)
    assert unpinned.pinned is False


async def test_set_pinned_unknown_id_raises(store):
    with pytest.raises(ValueError, match="No such chat session"):
        await store.set_pinned("nonexistent", True)


async def test_create_session_with_project_slug(store):
    info = await store.create_session(project_slug="climate")
    assert info.project_slug == "climate"
    assert (await store.get_session(info.id)).project_slug == "climate"


async def test_set_project_tags_and_clears(store):
    info = await store.create_session()
    tagged = await store.set_project(info.id, "climate")
    assert tagged.project_slug == "climate"

    cleared = await store.set_project(info.id, None)
    assert cleared.project_slug is None


async def test_set_project_unknown_id_raises(store):
    with pytest.raises(ValueError, match="No such chat session"):
        await store.set_project("nonexistent", "climate")


async def test_set_directory_sets_and_clears(store):
    info = await store.create_session()
    assert info.directory is None

    updated = await store.set_directory(info.id, r"D:\SomeFolder")
    assert updated.directory == r"D:\SomeFolder"
    assert (await store.get_session(info.id)).directory == r"D:\SomeFolder"

    cleared = await store.set_directory(info.id, None)
    assert cleared.directory is None


async def test_set_directory_unknown_id_raises(store):
    with pytest.raises(ValueError, match="No such chat session"):
        await store.set_directory("nonexistent", r"D:\SomeFolder")


async def test_directory_and_project_slug_are_independent(store):
    # Real bug this guards against: joining/leaving a Project must never
    # silently clear or overwrite a chat's own directory setting -- see
    # SessionInfo.directory's own docstring.
    info = await store.create_session()
    await store.set_directory(info.id, r"D:\SomeFolder")

    await store.set_project(info.id, "climate")
    assert (await store.get_session(info.id)).directory == r"D:\SomeFolder"

    await store.set_project(info.id, None)
    assert (await store.get_session(info.id)).directory == r"D:\SomeFolder"


async def test_reading_a_registry_without_a_directory_field_defaults_to_none(store, tmp_path):
    # Backward compatibility: a registry.json written before this field
    # existed has no "directory" key at all on older entries.
    info = await store.create_session()
    registry_path = tmp_path / "chat_sessions" / "registry.json"
    import json

    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    del raw[0]["directory"]
    registry_path.write_text(json.dumps(raw), encoding="utf-8")

    reloaded = await store.get_session(info.id)
    assert reloaded.directory is None


async def test_list_sessions_filtered_by_project(store):
    climate_1 = await store.create_session(project_slug="climate")
    await store.create_session(project_slug="other")
    unaffiliated = await store.create_session()
    climate_2 = await store.create_session(project_slug="climate")

    listed = await store.list_sessions(project_slug="climate")
    assert {s.id for s in listed} == {climate_1.id, climate_2.id}
    assert unaffiliated.id not in {s.id for s in listed}


async def test_list_sessions_unaffiliated_excludes_every_tagged_session(store):
    tagged_a = await store.create_session(project_slug="climate")
    tagged_b = await store.create_session(project_slug="other")
    plain_1 = await store.create_session()
    plain_2 = await store.create_session()

    listed = await store.list_sessions(unaffiliated=True)
    assert {s.id for s in listed} == {plain_1.id, plain_2.id}
    assert tagged_a.id not in {s.id for s in listed}
    assert tagged_b.id not in {s.id for s in listed}


async def test_rename_session_persists_and_rejects_empty(store):
    info = await store.create_session()
    updated = await store.rename_session(info.id, "  My renamed chat  ")
    assert updated.title == "My renamed chat"

    reloaded = await store.get_session(info.id)
    assert reloaded.title == "My renamed chat"

    with pytest.raises(ValueError, match="empty"):
        await store.rename_session(info.id, "   ")


async def test_rename_session_unknown_id_raises(store):
    with pytest.raises(ValueError, match="No such chat session"):
        await store.rename_session("nonexistent", "New title")


async def test_delete_session_removes_registry_entry_and_event_file(store):
    info = await store.create_session()
    store.append_event_sync(info.id, {"event_type": "user_input", "payload": {"text": "hi"}})

    await store.delete_session(info.id)

    assert await store.get_session(info.id) is None
    assert store.read_events(info.id) == []  # file gone -> reads back empty, not an error


async def test_delete_session_unknown_id_raises(store):
    with pytest.raises(ValueError, match="No such chat session"):
        await store.delete_session("nonexistent")


def test_append_event_sync_then_read_events_round_trips(store):
    import asyncio

    info = asyncio.run(store.create_session())
    store.append_event_sync(info.id, {"event_type": "user_input", "payload": {"text": "hello"}})
    store.append_event_sync(info.id, {"event_type": "final_answer", "payload": {"text": "hi there"}})

    events = store.read_events(info.id)
    assert [e["event_type"] for e in events] == ["user_input", "final_answer"]
    assert events[0]["payload"]["text"] == "hello"


def test_read_events_for_unknown_session_returns_empty_list(store):
    assert store.read_events("nonexistent") == []
