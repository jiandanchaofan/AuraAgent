"""Tests for core/bootstrap.py::build_app_context() — the one shared
composition root both main.py (CLI) and the GUI backend call. Uses real
project config (config/agents.json, config/mcp_servers.json, skills_store/)
so this exercises the exact same wiring a real `python main.py` run does,
while keeping all per-user sandboxed state (notes/calendar/tasks/memory/
workspace/logs) under tmp_path and never touching a real API key or the
developer's .env (`_env_file=None`, matching the rest of this test suite's
"no real API key needed" convention).
"""
from __future__ import annotations

import pytest

from confirmation.base import ConfirmationRequest
from config.settings import Settings
from core.bootstrap import build_app_context
from core.logger import AuraLogger, LogSink
from tests.fakes import FakeConfirmationChannel


def _test_settings(tmp_path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        notes_sandbox_root=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        _env_file=None,
        # agents_config_path / mcp_config_path / skills_dir deliberately
        # left at their real project defaults, so this exercises the real
        # shipped team + real MCP config + real skills_store.
    )


class _RecordingSink(LogSink):
    def __init__(self) -> None:
        self.events = []

    def write(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_build_app_context_wires_the_real_team_and_tools(tmp_path):
    logger = AuraLogger([_RecordingSink()])
    confirmation = FakeConfirmationChannel(decision=True)

    ctx = await build_app_context(_test_settings(tmp_path), confirmation, logger)
    try:
        assert ctx.agent_registry.leader.name == "orchestrator"
        worker_names = {w.name for w in ctx.agent_registry.workers}
        assert worker_names == {"researcher", "scheduler"}

        tool_names = {s.name for s in ctx.registry.get_tool_specs()}
        # A representative sample across every tool source: native,
        # MCP, Skill, and self-extension -- all converge on the same registry.
        assert {"create_note", "list_directory", "calculate", "find_capability", "propose_new_skill"} <= tool_names
        assert "mcp_example_reverse_text" in tool_names  # the real example MCP server connected
        assert "market_new_products" in tool_names  # a real shipped Skill

        leader_visible = {s.name for s in ctx.leader_view.get_tool_specs()}
        assert "propose_new_skill" in leader_visible
        assert "delegate_to_researcher" in leader_visible
        assert "delegate_to_scheduler" in leader_visible

        # cli_context is built from the same objects, so /agents (etc.)
        # dispatched from a GUI frontend would see the identical team.
        assert ctx.cli_context.agent_registry is ctx.agent_registry
        assert ctx.cli_context.registry is ctx.registry
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_build_app_context_orchestrator_is_runnable(tmp_path):
    """A cheap end-to-end smoke test that the returned orchestrator is a
    real, usable LeaderWorkerOrchestrator -- not just correctly-shaped
    data. Doesn't call the real LLM: swaps in a FakeLLMProvider after
    construction (mirrors how other tests replace SwappableProvider's
    current provider at runtime)."""
    from core.message_types import LLMResponse
    from tests.fakes import FakeLLMProvider

    logger = AuraLogger([_RecordingSink()])
    confirmation = FakeConfirmationChannel(decision=True)
    ctx = await build_app_context(_test_settings(tmp_path), confirmation, logger)
    try:
        fake = FakeLLMProvider(
            [LLMResponse(thought_text="hello!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
        )
        ctx.provider.set_current(fake, "anthropic")

        result = await ctx.orchestrator.run("say hi")

        assert result == "hello!"
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_build_app_context_confirmation_channel_is_reused_everywhere(tmp_path):
    """The SAME confirmation_channel instance passed in must be the one
    every gated tool (calendar/task/file delete, all six self-extension
    tools) actually uses -- not a fresh one built internally."""
    logger = AuraLogger([_RecordingSink()])
    confirmation = FakeConfirmationChannel(decision=True)

    ctx = await build_app_context(_test_settings(tmp_path), confirmation, logger)
    try:
        assert ctx.confirmation_channel is confirmation
        await ctx.registry.dispatch("write_file", {"path": "a.txt", "content": "x"})
        await ctx.registry.dispatch("delete_file", {"path": "a.txt"})
        assert len(confirmation.requests) == 1
        assert isinstance(confirmation.requests[0], ConfirmationRequest)
    finally:
        await ctx.aclose()
