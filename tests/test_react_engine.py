"""Verifies the ReAct loop's control flow using FakeLLMProvider — no
network calls. Covers: finishing without tools, dispatching a tool and
feeding its Observation back, the max_turns safety valve, concurrent
multi-tool-call dispatch within one turn, and agent_name propagation to
the logger.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.exceptions import MaxTurnsExceededError
from core.logger import AuraLogger
from core.message_types import LLMResponse, ToolCallRequest
from core.react_engine import AsyncReActEngine
from tests.fakes import FakeLLMProvider
from tools.base import ToolSpec
from tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_engine_completes_without_tool_use(tmp_path):
    provider = FakeLLMProvider(
        [LLMResponse(thought_text="Hello!", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    engine = AsyncReActEngine(provider, ToolRegistry(), AuraLogger(tmp_path), "system")

    result = await engine.run("hi")

    assert result == "Hello!"


@pytest.mark.asyncio
async def test_engine_dispatches_tool_and_feeds_observation_back(tmp_path):
    registry = ToolRegistry()
    calls = []

    async def handler(args):
        calls.append(args)
        return "42"

    registry.register(
        ToolSpec(name="add", description="adds", input_schema={"type": "object", "properties": {}}),
        handler,
    )

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text="I should add.",
                tool_calls=[ToolCallRequest(call_id="call_1", tool_name="add", arguments={"a": 1, "b": 2})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(
                thought_text="The answer is 42.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]
            ),
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system")
    result = await engine.run("what is 1+2?")

    assert result == "The answer is 42."
    assert calls == [{"a": 1, "b": 2}]

    # The second send() call's history must include a tool_results turn
    # carrying the Observation, or the conversation would be malformed.
    second_call_history = provider.sent_histories[1]
    assert second_call_history[-1].tool_results[0].content == "42"
    assert second_call_history[-1].tool_results[0].call_id == "call_1"


@pytest.mark.asyncio
async def test_engine_raises_on_max_turns_exceeded(tmp_path):
    registry = ToolRegistry()

    async def noop_handler(args):
        return "ok"

    registry.register(
        ToolSpec(name="noop", description="", input_schema={"type": "object", "properties": {}}), noop_handler
    )

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("c1", "noop", {})],
                stop_reason="tool_use",
                raw_provider_message=[],
            )
            for _ in range(3)
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system", max_turns=3)

    with pytest.raises(MaxTurnsExceededError):
        await engine.run("loop forever")


@pytest.mark.asyncio
async def test_engine_turns_unknown_tool_into_error_observation_not_a_crash(tmp_path):
    registry = ToolRegistry()  # nothing registered

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("c1", "missing_tool", {})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(
                thought_text="That tool doesn't exist.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]
            ),
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system")
    result = await engine.run("call a missing tool")

    assert result == "That tool doesn't exist."
    second_call_history = provider.sent_histories[1]
    assert second_call_history[-1].tool_results[0].is_error is True


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_turn_all_dispatch_and_map_back_by_call_id(tmp_path):
    registry = ToolRegistry()

    async def handler(args):
        return f"got {args['x']}"

    registry.register(
        ToolSpec(name="echo", description="", input_schema={"type": "object", "properties": {}}), handler
    )

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[
                    ToolCallRequest("c1", "echo", {"x": 1}),
                    ToolCallRequest("c2", "echo", {"x": 2}),
                    ToolCallRequest("c3", "echo", {"x": 3}),
                ],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(thought_text="done", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system")
    await engine.run("echo three things")

    tool_results = provider.sent_histories[1][-1].tool_results
    by_call_id = {r.call_id: r.content for r in tool_results}
    assert by_call_id == {"c1": "got 1", "c2": "got 2", "c3": "got 3"}


@pytest.mark.asyncio
async def test_one_failing_tool_call_does_not_corrupt_the_others_in_the_same_turn(tmp_path):
    registry = ToolRegistry()

    async def ok_handler(args):
        return "fine"

    async def broken_handler(args):
        raise ValueError("boom")

    registry.register(ToolSpec(name="ok", description="", input_schema={"type": "object", "properties": {}}), ok_handler)
    registry.register(
        ToolSpec(name="broken", description="", input_schema={"type": "object", "properties": {}}), broken_handler
    )

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[
                    ToolCallRequest("c1", "ok", {}),
                    ToolCallRequest("c2", "broken", {}),
                    ToolCallRequest("c3", "ok", {}),
                ],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(thought_text="done", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system")
    await engine.run("run three, one fails")

    tool_results = provider.sent_histories[1][-1].tool_results
    by_call_id = {r.call_id: r for r in tool_results}
    assert by_call_id["c1"].content == "fine" and by_call_id["c1"].is_error is False
    assert by_call_id["c3"].content == "fine" and by_call_id["c3"].is_error is False
    assert by_call_id["c2"].is_error is True


@pytest.mark.asyncio
async def test_tool_calls_in_one_turn_actually_run_concurrently_not_sequentially(tmp_path):
    """Timing-based proof, not just "both eventually complete": two
    handlers that each sleep run in well under 2x one sleep's duration if
    dispatched via asyncio.gather, but would take ~2x if the engine still
    used a sequential for loop."""
    registry = ToolRegistry()

    async def slow_handler(args):
        await asyncio.sleep(0.15)
        return "done"

    registry.register(
        ToolSpec(name="slow", description="", input_schema={"type": "object", "properties": {}}), slow_handler
    )

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text=None,
                tool_calls=[ToolCallRequest("c1", "slow", {}), ToolCallRequest("c2", "slow", {})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(thought_text="done", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
        ]
    )

    engine = AsyncReActEngine(provider, registry, AuraLogger(tmp_path), "system")

    start = asyncio.get_event_loop().time()
    await engine.run("run two slow things")
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 0.25  # well under 0.30s (2x sequential), comfortably above 0.15s (1x concurrent)


@pytest.mark.asyncio
async def test_agent_name_propagates_to_every_logger_call(tmp_path):
    registry = ToolRegistry()

    async def handler(args):
        return "ok"

    registry.register(ToolSpec(name="noop", description="", input_schema={"type": "object", "properties": {}}), handler)

    provider = FakeLLMProvider(
        [
            LLMResponse(
                thought_text="thinking",
                tool_calls=[ToolCallRequest("c1", "noop", {})],
                stop_reason="tool_use",
                raw_provider_message=[],
            ),
            LLMResponse(thought_text="final", tool_calls=[], stop_reason="end_turn", raw_provider_message=[]),
        ]
    )

    logger = AuraLogger(tmp_path)
    engine = AsyncReActEngine(provider, registry, logger, "system", agent_name="tester")
    await engine.run("hi")

    log_files = list(tmp_path.glob("session-*.jsonl"))
    assert len(log_files) == 1
    records = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines()]
    assert records  # sanity: something was logged
    assert all(r["agent_name"] == "tester" for r in records)
