"""Verifies the ReAct loop's control flow using FakeLLMProvider — no
network calls. Covers: finishing without tools, dispatching a tool and
feeding its Observation back, and the max_turns safety valve.
"""
from __future__ import annotations

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
