"""Unit tests for UserProfileStore's persistence + merge semantics,
independent of the tool/ToolRegistry layer (see test_user_profile_tool.py).
Mirrors tests/test_memory_store.py's style.
"""
from __future__ import annotations

import asyncio

import pytest

from tools.profile.user_profile_store import UserProfileStore


@pytest.mark.asyncio
async def test_new_store_starts_empty(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")

    profile = await store.get_profile()

    assert profile.is_empty()
    assert profile.render() == ""


@pytest.mark.asyncio
async def test_update_sets_fields_and_they_persist(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")

    await store.update_profile(preferences=["dark mode"], habits=["reviews PRs in the morning"])
    profile = await store.get_profile()

    assert profile.preferences == ["dark mode"]
    assert profile.habits == ["reviews PRs in the morning"]
    assert not profile.is_empty()


@pytest.mark.asyncio
async def test_repeated_list_updates_merge_and_dedupe(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")

    await store.update_profile(preferences=["dark mode"])
    await store.update_profile(preferences=["dark mode", "concise answers"])
    profile = await store.get_profile()

    assert profile.preferences == ["dark mode", "concise answers"]  # no duplicate


@pytest.mark.asyncio
async def test_notes_are_appended_not_replaced(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")

    await store.update_profile(notes="Works in product management.")
    await store.update_profile(notes="Based in Singapore.")
    profile = await store.get_profile()

    assert "Works in product management." in profile.notes
    assert "Based in Singapore." in profile.notes


@pytest.mark.asyncio
async def test_omitted_fields_are_left_unchanged(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    await store.update_profile(preferences=["dark mode"], notes="first note")

    await store.update_profile(habits=["stand-up at 9am"])
    profile = await store.get_profile()

    assert profile.preferences == ["dark mode"]
    assert profile.notes == "first note"
    assert profile.habits == ["stand-up at 9am"]


@pytest.mark.asyncio
async def test_data_persists_across_store_instances(tmp_path):
    file_path = tmp_path / "profile.json"
    await UserProfileStore(file_path).update_profile(common_topics=["market research"])

    reloaded = UserProfileStore(file_path)
    profile = await reloaded.get_profile()

    assert profile.common_topics == ["market research"]


@pytest.mark.asyncio
async def test_render_includes_all_populated_sections(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    await store.update_profile(
        preferences=["dark mode"], habits=["async standups"], common_topics=["EVs"], notes="PM at Acme."
    )

    text = (await store.get_profile()).render()

    assert "dark mode" in text
    assert "async standups" in text
    assert "EVs" in text
    assert "PM at Acme." in text


@pytest.mark.asyncio
async def test_concurrent_updates_do_not_lose_data(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")

    await asyncio.gather(*(store.update_profile(preferences=[f"pref-{i}"]) for i in range(20)))

    profile = await store.get_profile()
    assert len(profile.preferences) == 20
    assert len(set(profile.preferences)) == 20
