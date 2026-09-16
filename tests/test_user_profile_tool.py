"""Tests for the update_user_profile tool at the ToolRegistry dispatch
layer. Mirrors tests/test_memory_tool.py's style.
"""
from __future__ import annotations

import pytest

from tools.profile.user_profile_store import UserProfileStore
from tools.profile.user_profile_tool import register_user_profile_tools
from tools.registry import ToolRegistry


@pytest.fixture
def registry_and_store(tmp_path):
    registry = ToolRegistry()
    store = UserProfileStore(tmp_path / "profile.json")
    register_user_profile_tools(registry, store)
    return registry, store


@pytest.mark.asyncio
async def test_update_user_profile_persists_via_the_store(registry_and_store):
    registry, store = registry_and_store

    result = await registry.dispatch("update_user_profile", {"preferences": ["concise answers"]})

    assert "Profile updated" in result
    assert "concise answers" in result
    profile = await store.get_profile()
    assert profile.preferences == ["concise answers"]


@pytest.mark.asyncio
async def test_update_user_profile_with_no_fields_reports_empty_profile(registry_and_store):
    registry, store = registry_and_store

    result = await registry.dispatch("update_user_profile", {})

    assert "(empty)" in result


@pytest.mark.asyncio
async def test_update_user_profile_appends_notes(registry_and_store):
    registry, store = registry_and_store

    await registry.dispatch("update_user_profile", {"notes": "Works in product management."})
    await registry.dispatch("update_user_profile", {"notes": "Prefers Chinese replies."})

    profile = await store.get_profile()
    assert "Works in product management." in profile.notes
    assert "Prefers Chinese replies." in profile.notes
