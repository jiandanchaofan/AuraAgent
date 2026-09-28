"""Tests for tools/projects/project_tool.py::update_project_summary --
in particular that a mid-session update takes effect on leader_engine's
system_prompt immediately (no restart), which is the whole point of
NOT reusing user_profile's "read once at startup" pattern for this.
"""
from __future__ import annotations

import pytest

from core.react_engine import AsyncReActEngine
from providers.swappable_provider import SwappableProvider
from tests.fakes import FakeLLMProvider, make_test_logger
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.projects.project_tool import register_project_tools
from tools.registry import ToolRegistry


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
