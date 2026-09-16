"""Tests for build_delegate_tool — the entire worker-as-tool mechanism,
verified end-to-end with a real AsyncReActEngine (FakeLLMProvider-driven,
no network) standing in for the worker, proving delegation is genuinely
indistinguishable from any other tool call to the Leader's engine.
"""
from __future__ import annotations

import pytest

from agents.agent_definition import AgentDefinition
from agents.delegate_tool import build_delegate_tool
from core.logger import AuraLogger
from core.message_types import LLMResponse
from core.react_engine import AsyncReActEngine
from tests.fakes import FakeLLMProvider
from tools.registry import ToolRegistry


def _worker_def(name="researcher", capabilities=("fetch_url",)) -> AgentDefinition:
    return AgentDefinition(
        name=name, role="worker", system_prompt="You are a worker.", capabilities=list(capabilities)
    )


def test_spec_name_and_schema(tmp_path):
    worker = _worker_def()
    fake_provider = FakeLLMProvider([])
    worker_engine = AsyncReActEngine(fake_provider, ToolRegistry(), AuraLogger(tmp_path), "sys")
    registered = build_delegate_tool(worker, worker_engine)

    assert registered.spec.name == "delegate_to_researcher"
    assert "researcher" in registered.spec.description
    assert "fetch_url" in registered.spec.description
    assert registered.spec.input_schema["required"] == ["task"]


@pytest.mark.asyncio
async def test_handler_runs_the_worker_engine_and_returns_its_final_answer(tmp_path):
    worker_provider = FakeLLMProvider(
        [LLMResponse(thought_text="The answer is 42.", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    worker_engine = AsyncReActEngine(worker_provider, ToolRegistry(), AuraLogger(tmp_path), "sys", agent_name="researcher")
    registered = build_delegate_tool(_worker_def(), worker_engine)

    result = await registered.handler({"task": "what is 6*7?"})

    assert result == "The answer is 42."
    # The worker's own engine received the delegated task as its user_input.
    assert worker_provider.sent_histories[0][0].text == "what is 6*7?"
