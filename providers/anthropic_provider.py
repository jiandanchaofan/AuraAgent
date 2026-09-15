"""Anthropic Claude implementation of LLMProvider.

Uses the official `anthropic` SDK's AsyncAnthropic client and Claude's
native tool-use content blocks directly — no agent-loop wrapper. This is
the "manual agentic loop" pattern: every raw request/response is visible
right here, which is what makes core/react_engine.py's Thought/Tool
Call/Observation logging a faithful window into the actual API traffic.
"""
from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from core.message_types import ConversationTurn, LLMResponse, ToolCallRequest
from providers.base import LLMProvider
from tools.base import ToolSpec


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int = 4096) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self.model_name = model
        self._max_tokens = max_tokens

    def _translate_tool_specs(self, tool_specs: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
            for spec in tool_specs
        ]

    def _translate_history(self, history: list[ConversationTurn]) -> list[dict[str, Any]]:
        """Rebuild Anthropic's native `messages` list from provider-neutral
        history. Tool results must be sent back as a `user` message containing
        one `tool_result` block per call — batched together when the previous
        assistant turn made multiple parallel tool calls."""
        messages: list[dict[str, Any]] = []
        for turn in history:
            if turn.role == "assistant":
                messages.append({"role": "assistant", "content": turn.raw})
            elif turn.tool_results is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": result.call_id,
                                "content": result.content,
                                "is_error": result.is_error,
                            }
                            for result in turn.tool_results
                        ],
                    }
                )
            else:
                messages.append({"role": "user", "content": turn.text or ""})
        return messages

    async def send(
        self,
        system_prompt: str,
        history: list[ConversationTurn],
        tool_specs: list[ToolSpec],
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=self._translate_history(history),
            tools=self._translate_tool_specs(tool_specs),
        )

        thought_parts = [block.text for block in response.content if block.type == "text"]
        tool_calls = [
            ToolCallRequest(call_id=block.id, tool_name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]

        if response.stop_reason == "tool_use":
            stop_reason = "tool_use"
        elif response.stop_reason in ("end_turn", "stop_sequence"):
            stop_reason = "end_turn"
        elif response.stop_reason == "max_tokens":
            stop_reason = "max_tokens"
        else:
            stop_reason = "error"

        return LLMResponse(
            thought_text="\n".join(thought_parts) if thought_parts else None,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_provider_message=response.content,
        )
