"""Tests for SwappableProvider — the indirection layer /config use (the
CLI command) relies on to switch every AsyncReActEngine in the running
system over to a new provider/model without reaching into each engine."""
from __future__ import annotations

import pytest

from core.message_types import LLMResponse
from providers.swappable_provider import SwappableProvider
from tests.fakes import FakeLLMProvider


@pytest.mark.asyncio
async def test_delegates_send_to_the_current_provider():
    inner = FakeLLMProvider(
        [LLMResponse(thought_text="hi", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    swappable = SwappableProvider(inner, "anthropic")

    response = await swappable.send("sys", [], [])

    assert response.thought_text == "hi"
    assert len(inner.sent_histories) == 1


def test_model_name_reflects_the_current_provider():
    inner = FakeLLMProvider([])
    swappable = SwappableProvider(inner, "anthropic")

    assert swappable.model_name == "fake-model"


@pytest.mark.asyncio
async def test_set_current_switches_both_send_target_and_model_name():
    first = FakeLLMProvider(
        [LLMResponse(thought_text="from first", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    second = FakeLLMProvider(
        [LLMResponse(thought_text="from second", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    second.model_name = "fake-model-2"
    swappable = SwappableProvider(first, "anthropic")

    swappable.set_current(second, "openai")

    assert swappable.provider_name == "openai"
    assert swappable.model_name == "fake-model-2"
    response = await swappable.send("sys", [], [])
    assert response.thought_text == "from second"
    assert first.sent_histories == []  # the old provider was never called again
