"""Tests for tools/scheduler/scheduler_tool.py::propose_scheduled_task --
the LLM-facing way to set up a task that runs later without a human
re-triggering it. Mirrors tests/test_project_tool.py's harness style.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
from tools.scheduler.schedule_store import ScheduleStore
from tools.scheduler.scheduler_tool import register_propose_schedule_tool


def _build(tmp_path, decision: bool = True):
    registry = ToolRegistry()
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    project_store = ProjectStore(
        registry_path=tmp_path / "project_meta" / "registry.json",
        meta_dir=tmp_path / "project_meta",
        summary_max_chars=4_000,
    )
    active = ActiveProjectState()
    confirmation = FakeConfirmationChannel(decision=decision)
    register_propose_schedule_tool(registry, schedule_store, project_store, active, confirmation)
    return registry, schedule_store, project_store, active, confirmation


@pytest.mark.asyncio
async def test_propose_once_creates_schedule_when_approved(tmp_path):
    registry, schedule_store, _project_store, _active, confirmation = _build(tmp_path)

    result = await registry.dispatch(
        "propose_scheduled_task",
        {"task": "remind me to renew insurance", "trigger_type": "once", "run_at": "2026-10-05T15:00:00"},
    )

    assert "Scheduled" in result
    schedules = await schedule_store.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].task == "remind me to renew insurance"
    assert schedules[0].trigger_type == "once"
    assert len(confirmation.requests) == 1
    assert confirmation.requests[0].risk_level == "recurring_autonomy"


@pytest.mark.asyncio
async def test_propose_recurring_creates_schedule_when_approved(tmp_path):
    registry, schedule_store, _project_store, _active, _confirmation = _build(tmp_path)

    result = await registry.dispatch(
        "propose_scheduled_task",
        {"task": "daily OpenAI insight", "trigger_type": "recurring", "cron_expression": "0 9 * * *"},
    )

    assert "Scheduled" in result
    schedules = await schedule_store.list_schedules()
    assert schedules[0].trigger_type == "recurring"
    assert schedules[0].cron_expression == "0 9 * * *"


@pytest.mark.asyncio
async def test_propose_declined_creates_nothing(tmp_path):
    registry, schedule_store, _project_store, _active, _confirmation = _build(tmp_path, decision=False)

    result = await registry.dispatch(
        "propose_scheduled_task", {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    )

    assert "declined" in result
    assert await schedule_store.list_schedules() == []


@pytest.mark.asyncio
async def test_propose_invalid_trigger_type_raises(tmp_path):
    registry, _schedule_store, _project_store, _active, _confirmation = _build(tmp_path)

    with pytest.raises(ToolExecutionError, match="Invalid trigger_type"):
        await registry.dispatch("propose_scheduled_task", {"task": "x", "trigger_type": "sometimes"})


@pytest.mark.asyncio
async def test_propose_defaults_to_currently_active_project(tmp_path):
    registry, schedule_store, project_store, active, _confirmation = _build(tmp_path)
    await project_store.create_project("ai_governance", "ai_governance", tmp_path / "ai_governance")
    active.current_slug = "ai_governance"

    await registry.dispatch(
        "propose_scheduled_task", {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    )

    schedules = await schedule_store.list_schedules()
    assert schedules[0].project_slug == "ai_governance"


@pytest.mark.asyncio
async def test_propose_explicit_project_overrides_active(tmp_path):
    registry, schedule_store, project_store, active, _confirmation = _build(tmp_path)
    await project_store.create_project("p1", "p1", tmp_path / "p1")
    await project_store.create_project("p2", "p2", tmp_path / "p2")
    active.current_slug = "p1"

    await registry.dispatch(
        "propose_scheduled_task",
        {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project": "p2"},
    )

    schedules = await schedule_store.list_schedules()
    assert schedules[0].project_slug == "p2"


@pytest.mark.asyncio
async def test_propose_unknown_explicit_project_raises(tmp_path):
    registry, _schedule_store, _project_store, _active, _confirmation = _build(tmp_path)

    with pytest.raises(ToolExecutionError, match="No project named"):
        await registry.dispatch(
            "propose_scheduled_task",
            {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *", "project": "ghost"},
        )


@pytest.mark.asyncio
async def test_propose_unaffiliated_when_no_active_project_and_none_named(tmp_path):
    registry, schedule_store, _project_store, active, _confirmation = _build(tmp_path)
    assert active.current_slug is None

    await registry.dispatch(
        "propose_scheduled_task", {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    )

    schedules = await schedule_store.list_schedules()
    assert schedules[0].project_slug is None


@pytest.mark.asyncio
async def test_confirmation_reason_mentions_unattended_and_auto_decline(tmp_path):
    registry, _schedule_store, _project_store, _active, confirmation = _build(tmp_path)

    await registry.dispatch(
        "propose_scheduled_task", {"task": "x", "trigger_type": "recurring", "cron_expression": "0 9 * * *"}
    )

    reason = confirmation.requests[0].reason
    assert "自动拒绝" in reason or "auto" in reason.lower()
