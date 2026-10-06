"""Tests for tools/scheduler/scheduler_loop.py -- the trickiest part of
N8 proactivity: a scheduled run must resolve its own directory/Project
context the exact same way a live turn does (cli/service.py::
sync_active_directory()), auto-decline confirmations for its own
duration (restored after -- this part IS still a swap-then-restore, see
that module's own docstring for why it's a different, orthogonal
concern), and never run concurrently with whatever AppContext.run_lock is
also guarding.

_run_one() deliberately does NOT restore workspace_root/active_project/
the Leader's dynamic tool patterns after running (an earlier version did
-- see ActiveProjectState's own docstring for the bug that caused): the
NEXT turn to run, live or scheduled, resolves its own correct state fresh
regardless of what this one leaves behind, so several tests below assert
the POST-run state still reflects whatever this schedule itself resolved
to, not some restored "previous" value.

A lightweight duck-typed context object stands in for the real AppContext
(scheduler_loop.py only ever type-hints it under TYPE_CHECKING, never
isinstance-checks it) -- this keeps these tests focused on the
scheduler's OWN logic without paying for a real build_app_context() (MCP
connections, Skill loading, ...). It also stands in for sync_active_directory()'s
own `ctx: CLIContext` parameter the same structural-typing way (see that
function's own docstring) -- `mcp_manager` here is a trivial fake with a
no-op sync_directory_following_servers(), not a real MCPClientManager.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from agents.leader_worker_orchestrator import LeaderWorkerOrchestrator
from agents.scoped_tool_registry import ScopedToolRegistryView
from confirmation.swappable_channel import SwappableConfirmationChannel
from core.logger import AuraLogger
from core.message_types import LLMResponse, ToolCallRequest
from core.react_engine import AsyncReActEngine
from providers.swappable_provider import SwappableProvider
from tests.fakes import FakeConfirmationChannel, FakeLLMProvider, make_test_logger
from tools.base import ToolSpec
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
from tools.scheduler.schedule_store import ScheduleStore
from tools.scheduler.scheduler_loop import _run_one, run_scheduler_loop
from tools.workspace_root import SwappableWorkspaceRoot


@pytest.fixture(autouse=True)
def _no_real_desktop_notifications(monkeypatch):
    """_run_one() always ends by trying to send a desktop notification
    (tools/scheduler/scheduler_loop.py::_notify) -- a no-op stub here by
    default so the bulk of this file's tests don't pop a real system toast;
    the two tests specifically about _notify's own behavior override this
    with their own monkeypatch.setattr call. Same convention as
    tests/test_notification_tool.py."""
    monkeypatch.setattr("plyer.notification.notify", lambda **kwargs: None)


class _FakeMCPManager:
    """sync_active_directory() calls mcp_manager.sync_directory_following_servers()
    unconditionally -- a trivial no-op stand-in, since these tests never
    configure a real MCP server and have no interest in that side effect."""

    async def sync_directory_following_servers(self, directory) -> None:
        pass


@dataclass
class _Ctx:
    schedule_store: ScheduleStore
    project_store: ProjectStore
    active_project: ActiveProjectState
    workspace_root: SwappableWorkspaceRoot
    default_workspace_root: SwappableWorkspaceRoot
    mcp_manager: object
    leader_view: ScopedToolRegistryView
    confirmation_channel: SwappableConfirmationChannel
    leader_engine: AsyncReActEngine
    base_leader_system_prompt: str
    orchestrator: LeaderWorkerOrchestrator
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    logger: object = None


def _build_ctx(tmp_path, responses):
    registry = ToolRegistry()
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    project_store = ProjectStore(
        registry_path=tmp_path / "project_meta" / "registry.json",
        meta_dir=tmp_path / "project_meta",
        summary_max_chars=4_000,
    )
    active_project = ActiveProjectState()
    workspace_root = SwappableWorkspaceRoot(tmp_path / "workspace")
    default_workspace_root = SwappableWorkspaceRoot(tmp_path / "workspace")
    logger = make_test_logger(tmp_path / "logs")
    real_confirmation = FakeConfirmationChannel(decision=True)
    confirmation_channel = SwappableConfirmationChannel(real_confirmation)
    provider = SwappableProvider(FakeLLMProvider(responses), "anthropic")
    leader_view = ScopedToolRegistryView(registry, ["*"])
    leader_engine = AsyncReActEngine(
        provider=provider, registry=leader_view, logger=logger, system_prompt="BASE PROMPT", agent_name="orchestrator"
    )
    orchestrator = LeaderWorkerOrchestrator(leader_engine)
    ctx = _Ctx(
        schedule_store=schedule_store,
        project_store=project_store,
        active_project=active_project,
        workspace_root=workspace_root,
        default_workspace_root=default_workspace_root,
        mcp_manager=_FakeMCPManager(),
        leader_view=leader_view,
        confirmation_channel=confirmation_channel,
        leader_engine=leader_engine,
        base_leader_system_prompt="BASE PROMPT",
        orchestrator=orchestrator,
        logger=logger,
    )
    return ctx, registry, real_confirmation


def _tool_call_response(name="probe") -> LLMResponse:
    return LLMResponse(
        thought_text="using the probe",
        tool_calls=[ToolCallRequest(call_id="1", tool_name=name, arguments={})],
        stop_reason="tool_use",
        raw_provider_message=[],
    )


def _end_turn_response(text="done") -> LLMResponse:
    return LLMResponse(thought_text=text, tool_calls=[], stop_reason="end_turn", raw_provider_message=[])


@pytest.mark.asyncio
async def test_run_one_project_scoped_swaps_workspace_and_restores_after(tmp_path):
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response()])
    await ctx.project_store.create_project("p", "p", tmp_path / "p")
    await ctx.project_store.set_enabled_tools("p", ["mcp_example_*"])
    schedule = await ctx.schedule_store.create_schedule(
        task="do something", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="p"
    )
    original_workspace = ctx.workspace_root.current

    await _run_one(ctx, schedule)

    # Restored to exactly what it was before the run -- see module
    # docstring for why the scheduler still restores explicitly (routed
    # through sync_active_directory() again, not direct field mutation)
    # even though a GUI chat never needs this from its own side.
    assert ctx.workspace_root.current == original_workspace
    assert ctx.active_project.current_slug is None
    assert ctx.leader_view.get_dynamic_patterns() == []
    assert ctx.leader_engine.system_prompt == ctx.base_leader_system_prompt


@pytest.mark.asyncio
async def test_run_one_project_scoped_swaps_context_during_execution(tmp_path):
    captured = {}
    ctx, registry, _real = _build_ctx(tmp_path, [_tool_call_response(), _end_turn_response()])
    await ctx.project_store.create_project("p", "p", tmp_path / "p")
    await ctx.project_store.set_enabled_tools("p", ["mcp_example_*"])
    project = await ctx.project_store.get_project("p")

    async def probe(args):
        captured["workspace"] = ctx.workspace_root.current
        captured["project_slug"] = ctx.active_project.current_slug
        captured["dynamic_patterns"] = ctx.leader_view.get_dynamic_patterns()
        return "ok"

    registry.register(ToolSpec(name="probe", description="", input_schema={"type": "object", "properties": {}}), probe)

    schedule = await ctx.schedule_store.create_schedule(
        task="do something", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="p"
    )

    await _run_one(ctx, schedule)

    assert captured["workspace"] == project.directory
    assert captured["project_slug"] == "p"
    assert captured["dynamic_patterns"] == ["mcp_example_*"]


@pytest.mark.asyncio
async def test_run_one_unaffiliated_task_uses_the_persisted_default(tmp_path):
    # An unaffiliated task (no project_slug) resolves to
    # ctx.default_workspace_root -- exactly like an unaffiliated live chat
    # with no directory override of its own would (see
    # sync_active_directory()'s priority chain).
    ctx, registry, _real = _build_ctx(tmp_path, [_tool_call_response(), _end_turn_response()])
    captured = {}

    async def probe(args):
        captured["workspace"] = ctx.workspace_root.current
        captured["project_slug"] = ctx.active_project.current_slug
        return "ok"

    registry.register(ToolSpec(name="probe", description="", input_schema={"type": "object", "properties": {}}), probe)

    default_workspace = ctx.default_workspace_root.current
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    assert captured["workspace"] == default_workspace
    assert captured["project_slug"] is None
    assert ctx.workspace_root.current == default_workspace


@pytest.mark.asyncio
async def test_run_one_unaffiliated_task_clears_a_stale_active_project_during_run(tmp_path):
    """If the last live session left active_project pointing at some
    project (its normal, expected state between turns), an unaffiliated
    scheduled task must not inherit it -- and it's restored after, since
    the CLI's "current project" has no persisted source of its own to
    re-derive it from otherwise (see module docstring)."""
    ctx, registry, _real = _build_ctx(tmp_path, [_tool_call_response(), _end_turn_response()])
    await ctx.project_store.create_project("stale", "stale", tmp_path / "stale")
    ctx.active_project.current_slug = "stale"
    captured = {}

    async def probe(args):
        captured["project_slug"] = ctx.active_project.current_slug
        return "ok"

    registry.register(ToolSpec(name="probe", description="", input_schema={"type": "object", "properties": {}}), probe)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    assert captured["project_slug"] is None  # cleared during the run
    assert ctx.active_project.current_slug == "stale"  # restored after


@pytest.mark.asyncio
async def test_run_one_confirmation_channel_auto_declines_during_run_and_restores_after(tmp_path):
    ctx, registry, real_confirmation = _build_ctx(tmp_path, [_tool_call_response(), _end_turn_response()])
    captured = {}

    async def probe(args):
        from confirmation.base import ConfirmationRequest

        captured["decision"] = await ctx.confirmation_channel.confirm(
            ConfirmationRequest(tool_name="probe", arguments={}, reason="x", risk_level="destructive")
        )
        return "ok"

    registry.register(ToolSpec(name="probe", description="", input_schema={"type": "object", "properties": {}}), probe)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    assert captured["decision"] is False  # auto-declined during the run
    assert ctx.confirmation_channel.current is real_confirmation  # restored after
    assert len(real_confirmation.requests) == 0  # the real channel never saw it


@pytest.mark.asyncio
async def test_run_one_records_result_after_running(tmp_path):
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("the insight is X")])
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert updated.last_run_at is not None
    assert "the insight is X" in updated.last_result_summary


@pytest.mark.asyncio
async def test_run_one_failed_orchestrator_run_still_restores_confirmation_and_records_failure(tmp_path):
    ctx, registry, real_confirmation = _build_ctx(tmp_path, [])  # no scripted responses -> FakeLLMProvider raises
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)  # must not raise

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert updated.last_result_summary.startswith("Failed:")
    assert ctx.confirmation_channel.current is real_confirmation  # restore path ran despite the failure


@pytest.mark.asyncio
async def test_run_one_missing_project_skips_without_invoking_orchestrator(tmp_path):
    ctx, registry, _real = _build_ctx(tmp_path, [])  # would raise if orchestrator.run() were ever called
    schedule = await ctx.schedule_store.create_schedule(
        task="x", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="ghost_project"
    )

    await _run_one(ctx, schedule)  # must not raise despite zero scripted LLM responses

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert "Skipped" in updated.last_result_summary
    assert "No project named 'ghost_project'" in updated.last_result_summary


@pytest.mark.asyncio
async def test_run_one_waits_for_run_lock_held_by_a_live_turn(tmp_path):
    ctx, registry, _real = _build_ctx(tmp_path, [_tool_call_response(), _end_turn_response()])
    events: list[str] = []

    async def probe(args):
        events.append("scheduler_ran")
        return "ok"

    registry.register(ToolSpec(name="probe", description="", input_schema={"type": "object", "properties": {}}), probe)
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    async def hold_lock_like_a_live_turn():
        async with ctx.run_lock:
            events.append("live_turn_start")
            await asyncio.sleep(0.15)
            events.append("live_turn_end")

    live_turn_task = asyncio.create_task(hold_lock_like_a_live_turn())
    await asyncio.sleep(0.02)  # let the live turn actually acquire the lock first
    await _run_one(ctx, schedule)
    await live_turn_task

    assert events == ["live_turn_start", "live_turn_end", "scheduler_ran"]


@pytest.mark.asyncio
async def test_run_one_calls_notify_with_the_run_result(tmp_path, monkeypatch):
    # plyer.notification.notify() pops a REAL system toast on whatever
    # machine runs this test -- monkeypatched rather than called for real,
    # same convention as tests/test_notification_tool.py.
    calls = []
    monkeypatch.setattr(
        "plyer.notification.notify", lambda **kwargs: calls.append(kwargs)
    )
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("the insight is ready")])
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    assert len(calls) == 1
    assert "the insight is ready" in calls[0]["message"]


@pytest.mark.asyncio
async def test_run_one_notify_failure_does_not_affect_the_recorded_outcome(tmp_path, monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("no notification backend on this machine")

    monkeypatch.setattr("plyer.notification.notify", _raise)
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("the insight is ready")])
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)  # must not raise

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert "the insight is ready" in updated.last_result_summary


@pytest.mark.asyncio
async def test_run_one_emits_a_schedule_result_event(tmp_path):
    # N14 (Auralis / remote access): this is what gui/ws_log_sink.py's
    # WebSocketSink broadcasts to every connected device (see
    # core/logger.py::log_schedule_result's own docstring) -- a disconnected
    # phone catches up later via GET /api/schedules instead.
    events: list[dict] = []

    class _RecordingSink:
        def write(self, event):
            events.append(event)

    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("the insight is ready")])
    ctx.logger = AuraLogger([_RecordingSink()])
    schedule = await ctx.schedule_store.create_schedule(task="daily insight", trigger_type="recurring", cron_expression="0 9 * * *")

    await _run_one(ctx, schedule)

    result_events = [e for e in events if e["event_type"] == "schedule_result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["schedule_id"] == schedule.id
    assert result_events[0]["payload"]["task"] == "daily insight"
    assert "the insight is ready" in result_events[0]["payload"]["summary"]


@pytest.mark.asyncio
async def test_run_scheduler_loop_executes_a_due_schedule(tmp_path):
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("insight ready")])
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    schedule = await ctx.schedule_store.create_schedule(task="x", trigger_type="once", run_at=past)

    loop_task = asyncio.create_task(run_scheduler_loop(ctx, poll_interval_seconds=0.05))
    try:
        for _ in range(100):
            updated = await ctx.schedule_store.get_schedule(schedule.id)
            if updated.last_run_at is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("scheduler loop never ran the due schedule in time")
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    updated = await ctx.schedule_store.get_schedule(schedule.id)
    assert updated.enabled is False  # a "once" task disables itself after running
    assert "insight ready" in updated.last_result_summary


@pytest.mark.asyncio
async def test_run_scheduler_loop_survives_a_bad_iteration(tmp_path):
    """A schedule pointing at a project that will raise when looked up
    must not kill the whole loop -- the NEXT due schedule (if any) still
    has to get a chance to run on a later iteration."""
    ctx, registry, _real = _build_ctx(tmp_path, [_end_turn_response("ok")])
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    # A schedule referencing a project that doesn't exist is handled
    # gracefully by _run_one itself (see test above); to actually exercise
    # run_scheduler_loop's own try/except, make list_due raise once.
    original_list_due = ctx.schedule_store.list_due
    call_count = {"n": 0}

    async def flaky_list_due(now=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient failure")
        return await original_list_due(now)

    ctx.schedule_store.list_due = flaky_list_due
    await ctx.schedule_store.create_schedule(task="x", trigger_type="once", run_at=past)

    loop_task = asyncio.create_task(run_scheduler_loop(ctx, poll_interval_seconds=0.05))
    try:
        for _ in range(100):
            if call_count["n"] >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("scheduler loop did not recover from the first failed iteration")
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
