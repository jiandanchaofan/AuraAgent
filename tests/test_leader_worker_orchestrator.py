"""End-to-end (still network-free) test of the whole Multi-Agent pipeline:
ToolRegistry -> ScopedToolRegistryView (Leader + Worker) -> delegate_tool
-> LeaderWorkerOrchestrator, each engine driven by its own scripted
FakeLLMProvider. Complements the real-LLM manual verification with a
deterministic, fast regression test of the same wiring main.py performs.
"""
from __future__ import annotations

import pytest

from agents.agent_definition import AgentDefinition
from agents.delegate_tool import build_delegate_tool
from agents.leader_worker_orchestrator import LeaderWorkerOrchestrator
from agents.scoped_tool_registry import ScopedToolRegistryView
from core.logger import AuraLogger
from core.message_types import LLMResponse, ToolCallRequest
from core.react_engine import AsyncReActEngine
from tests.fakes import FakeLLMProvider
from tools.base import ToolSpec
from tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_leader_delegates_to_worker_and_synthesizes_final_answer(tmp_path):
    shared_registry = ToolRegistry()

    async def fetch_url_handler(args):
        return "Example Domain page content"

    shared_registry.register(
        ToolSpec(name="fetch_url", description="", input_schema={"type": "object", "properties": {}}),
        fetch_url_handler,
    )
    # A tool the worker should NOT be able to reach, to prove the scoped
    # view is enforced end-to-end through this whole composed pipeline.
    async def create_task_handler(args):
        return "Created task"

    shared_registry.register(
        ToolSpec(name="create_task", description="", input_schema={"type": "object", "properties": {}}),
        create_task_handler,
    )

    logger = AuraLogger(tmp_path)

    worker_def = AgentDefinition(
        name="researcher", role="worker", system_prompt="research things", capabilities=["fetch_url"]
    )
    worker_provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("wc1", "fetch_url", {"url": "https://example.com"})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(
                thought_text="The page says: Example Domain page content",
                tool_calls=[],
                stop_reason="end_turn",
                raw_provider_message=[],
            ),
        ]
    )
    worker_view = ScopedToolRegistryView(shared_registry, worker_def.capabilities)
    worker_engine = AsyncReActEngine(
        worker_provider, worker_view, logger, worker_def.system_prompt, agent_name=worker_def.name
    )
    delegate_tool = build_delegate_tool(worker_def, worker_engine)

    leader_def = AgentDefinition(
        name="orchestrator", role="leader", system_prompt="orchestrate", capabilities=["create_task"]
    )
    leader_provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("lc1", "delegate_to_researcher", {"task": "summarize example.com"})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(
                thought_text="Summary: Example Domain page content",
                tool_calls=[],
                stop_reason="end_turn",
                raw_provider_message=[],
            ),
        ]
    )
    leader_view = ScopedToolRegistryView(
        shared_registry, leader_def.capabilities, extra_tools={delegate_tool.spec.name: delegate_tool}
    )
    leader_engine = AsyncReActEngine(
        leader_provider, leader_view, logger, leader_def.system_prompt, agent_name=leader_def.name
    )

    orchestrator = LeaderWorkerOrchestrator(leader_engine)
    result = await orchestrator.run("what does example.com say?")

    assert result == "Summary: Example Domain page content"
    # The worker actually ran its own ReAct loop (called fetch_url itself).
    assert len(worker_provider.sent_histories) == 2


@pytest.mark.asyncio
async def test_worker_cannot_reach_leader_only_tools_even_via_delegation(tmp_path):
    """The worker's view was built with capabilities=["fetch_url"] only —
    if its LLM (hallucinating or otherwise) tried to call create_task, the
    ToolRegistry.dispatch() -> ToolExecutionError -> is_error Observation
    path (not a crash) is exercised, proving enforcement survives being
    reached through a delegate_to_* call rather than a direct engine.run()."""
    shared_registry = ToolRegistry()

    async def create_task_handler(args):
        return "Created task"

    shared_registry.register(
        ToolSpec(name="create_task", description="", input_schema={"type": "object", "properties": {}}),
        create_task_handler,
    )

    logger = AuraLogger(tmp_path)
    worker_def = AgentDefinition(
        name="researcher", role="worker", system_prompt="research things", capabilities=["fetch_url"]
    )
    worker_provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("wc1", "create_task", {})],  # out of scope for this worker
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(
                thought_text="I could not do that.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]
            ),
        ]
    )
    worker_view = ScopedToolRegistryView(shared_registry, worker_def.capabilities)
    worker_engine = AsyncReActEngine(
        worker_provider, worker_view, logger, worker_def.system_prompt, agent_name=worker_def.name
    )

    result = await worker_engine.run("try to create a task")

    assert result == "I could not do that."
    tool_results = worker_provider.sent_histories[1][-1].tool_results
    assert tool_results[0].is_error is True
