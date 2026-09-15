"""Future real implementation slot for an OpenAI-compatible LLMProvider.

Same LLMProvider contract as AnthropicProvider (providers/anthropic_provider.py):
translate ConversationTurn history into OpenAI's `messages` + `tools`
(function-calling) shape, and translate the response's `tool_calls` back
into provider-neutral ToolCallRequest objects. Swapping this in later
requires zero changes to core/react_engine.py or tools/registry.py — that
is the entire point of the LLMProvider abstraction.
"""
from __future__ import annotations

from core.message_types import ConversationTurn, LLMResponse
from providers.base import LLMProvider
from tools.base import ToolSpec


class OpenAIProvider(LLMProvider):
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "OpenAIProvider is not implemented yet — v1 uses AnthropicProvider. "
            "See providers/base.py for the LLMProvider contract to implement."
        )

    async def send(
        self,
        system_prompt: str,
        history: list[ConversationTurn],
        tool_specs: list[ToolSpec],
    ) -> LLMResponse:
        raise NotImplementedError
