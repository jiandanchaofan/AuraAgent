"""Tests for tools/projects/project_tool.py -- update_project_summary (in
particular that a mid-session update takes effect on leader_engine's
system_prompt immediately, no restart, which is the whole point of NOT
reusing user_profile's "read once at startup" pattern for this),
remember_project_fact/recall_project_facts (the per-project, pull-based
counterpart to global remember_fact/recall_facts), and
search_project_chats (a separate registration function -- see its own
docstring for why it needs a ChatSessionStore the other three don't).
"""
from __future__ import annotations

import pytest

from core.react_engine import AsyncReActEngine
from providers.swappable_provider import SwappableProvider
from tests.fakes import FakeLLMProvider, make_test_logger
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.projects.project_tool import register_project_chat_search_tool, register_project_tools
from tools.registry import ToolRegistry
from tools.sessions.session_store import ChatSessionStore


def _build(tmp_path):
    registry = ToolRegistry()
    store = ProjectStore(
        registry_path=tmp_path / "project_meta" / "registry.json",
        meta_dir=tmp_path / "project_meta",
        summary_max_chars=4_000,
    )
    active = ActiveProjectState()
    provider = SwappableProvider(FakeLLMProvider([]), "anthropic")
    logger = make_test_logger(tmp_path / "logs")
    leader_engine = AsyncReActEngine(
        provider=provider, registry=registry, logger=logger, system_prompt="BASE PROMPT", agent_name="orchestrator"
    )
    register_project_tools(registry, store, active, leader_engine, "BASE PROMPT")
    return registry, store, active, leader_engine


@pytest.mark.asyncio
async def test_update_with_no_active_project_reports_clearly(tmp_path):
    registry, _store, _active, _engine = _build(tmp_path)

    result = await registry.dispatch("update_project_summary", {"current_state": "x"})

    assert "No project is currently active" in result


@pytest.mark.asyncio
async def test_update_summary_immediately_syncs_leader_system_prompt(tmp_path):
    registry, store, active, leader_engine = _build(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    await registry.dispatch("update_project_summary", {"current_state": "researching EU AI Act"})

    assert "researching EU AI Act" in leader_engine.system_prompt
    assert leader_engine.system_prompt.startswith("BASE PROMPT")


@pytest.mark.asyncio
async def test_second_update_replaces_state_in_prompt_not_appends(tmp_path):
    registry, store, active, leader_engine = _build(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    await registry.dispatch("update_project_summary", {"current_state": "first"})
    await registry.dispatch("update_project_summary", {"current_state": "second"})

    assert "second" in leader_engine.system_prompt
    assert "first" not in leader_engine.system_prompt


@pytest.mark.asyncio
async def test_update_summary_role_syncs_leader_system_prompt(tmp_path):
    registry, store, active, leader_engine = _build(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    await registry.dispatch("update_project_summary", {"role": "Act as a compliance assistant"})

    assert "Act as a compliance assistant" in leader_engine.system_prompt


@pytest.mark.asyncio
async def test_remember_project_fact_with_no_active_project_reports_clearly(tmp_path):
    registry, _store, _active, _engine = _build(tmp_path)

    result = await registry.dispatch("remember_project_fact", {"content": "x"})

    assert "No project is currently active" in result


@pytest.mark.asyncio
async def test_recall_project_facts_with_no_active_project_reports_clearly(tmp_path):
    registry, _store, _active, _engine = _build(tmp_path)

    result = await registry.dispatch("recall_project_facts", {})

    assert "No project is currently active" in result


@pytest.mark.asyncio
async def test_remember_and_recall_project_fact_round_trip(tmp_path):
    registry, store, active, _engine = _build(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    remembered = await registry.dispatch("remember_project_fact", {"content": "The client's budget is $50k"})
    assert "$50k" in remembered

    recalled = await registry.dispatch("recall_project_facts", {"query": "budget"})
    assert "$50k" in recalled


@pytest.mark.asyncio
async def test_recall_project_facts_no_match_reports_clearly(tmp_path):
    registry, store, active, _engine = _build(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"
    await registry.dispatch("remember_project_fact", {"content": "unrelated fact"})

    result = await registry.dispatch("recall_project_facts", {"query": "nonexistent"})

    assert "No matching facts found" in result


@pytest.mark.asyncio
async def test_project_facts_are_isolated_from_a_different_project(tmp_path):
    registry, store, active, _engine = _build(tmp_path)
    await store.create_project("p1", "p1", tmp_path / "p1")
    await store.create_project("p2", "p2", tmp_path / "p2")

    active.current_slug = "p1"
    await registry.dispatch("remember_project_fact", {"content": "only in p1"})

    active.current_slug = "p2"
    result = await registry.dispatch("recall_project_facts", {})

    assert "only in p1" not in result


def _build_with_chat_search(tmp_path, current_session_id):
    registry, store, active, leader_engine = _build(tmp_path)
    session_store = ChatSessionStore(tmp_path / "chat_sessions")
    register_project_chat_search_tool(
        registry, active, session_store, lambda: current_session_id["value"], leader_name="orchestrator"
    )
    return registry, store, active, leader_engine, session_store


@pytest.mark.asyncio
async def test_search_project_chats_with_no_active_project_reports_clearly(tmp_path):
    registry, _store, _active, _engine, _sessions = _build_with_chat_search(tmp_path, {"value": None})

    result = await registry.dispatch("search_project_chats", {"query": "x"})

    assert "No project is currently active" in result


@pytest.mark.asyncio
async def test_search_project_chats_finds_a_match_in_another_chat(tmp_path):
    current = {"value": "current-session"}
    registry, store, active, _engine, sessions = _build_with_chat_search(tmp_path, current)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    other = await sessions.create_session(project_slug="p")
    sessions.append_event_sync(
        other.id, {"agent_name": "orchestrator", "event_type": "user_input", "payload": {"text": "the launch date is March 3rd"}}
    )

    result = await registry.dispatch("search_project_chats", {"query": "launch date"})

    assert "March 3rd" in result
    assert other.title in result


@pytest.mark.asyncio
async def test_search_project_chats_excludes_the_current_session(tmp_path):
    current = {"value": None}
    registry, store, active, _engine, sessions = _build_with_chat_search(tmp_path, current)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    this_chat = await sessions.create_session(project_slug="p")
    current["value"] = this_chat.id  # this IS the currently active session
    sessions.append_event_sync(
        this_chat.id, {"agent_name": "orchestrator", "event_type": "user_input", "payload": {"text": "unique keyword here"}}
    )

    result = await registry.dispatch("search_project_chats", {"query": "unique keyword"})

    assert "No matches found" in result


@pytest.mark.asyncio
async def test_search_project_chats_excludes_a_different_project(tmp_path):
    current = {"value": None}
    registry, store, active, _engine, sessions = _build_with_chat_search(tmp_path, current)
    await store.create_project("p1", "p1", tmp_path / "p1")
    await store.create_project("p2", "p2", tmp_path / "p2")

    other = await sessions.create_session(project_slug="p2")
    sessions.append_event_sync(
        other.id, {"agent_name": "orchestrator", "event_type": "user_input", "payload": {"text": "belongs to p2"}}
    )

    active.current_slug = "p1"
    result = await registry.dispatch("search_project_chats", {"query": "belongs to p2"})

    assert "No matches found" in result


@pytest.mark.asyncio
async def test_search_project_chats_ignores_worker_sub_task_events(tmp_path):
    current = {"value": None}
    registry, store, active, _engine, sessions = _build_with_chat_search(tmp_path, current)
    await store.create_project("p", "p", tmp_path / "p")
    active.current_slug = "p"

    other = await sessions.create_session(project_slug="p")
    sessions.append_event_sync(
        other.id, {"agent_name": "researcher", "event_type": "user_input", "payload": {"text": "worker-only keyword"}}
    )

    result = await registry.dispatch("search_project_chats", {"query": "worker-only keyword"})

    assert "No matches found" in result
