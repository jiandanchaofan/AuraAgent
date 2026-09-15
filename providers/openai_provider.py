"""OpenAI-compatible implementation of LLMProvider.

Uses the official `openai` Python SDK's AsyncOpenAI client against the
Chat Completions API with function-calling tools. DeepSeek (and many other
vendors) expose an OpenAI-compatible endpoint, so this same class serves
OpenAI itself or DeepSeek — only `base_url` and `model` differ. That is
exactly the seam providers/base.py's LLMProvider abstraction was built
for: swapping model vendors is a main.py composition change, never a
change to core/react_engine.py.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from core.message_types import ConversationTurn, LLMResponse, ToolCallRequest
from providers.base import LLMProvider
from tools.base import ToolSpec


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model
        self._max_tokens = max_tokens

    def _translate_tool_specs(self, tool_specs: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in tool_specs
        ]

    def _translate_history(self, system_prompt: str, history: list[ConversationTurn]) -> list[dict[str, Any]]:
        """Rebuild OpenAI's native `messages` list. Unlike Anthropic (which
        batches tool results into one user message with tool_result content
        blocks), the Chat Completions API requires one separate role="tool"
        message per call_id — so a single tool_results turn here expands
        into N messages."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in history:
            if turn.role == "assistant":
                messages.append(turn.raw)
            elif turn.tool_results is not None:
                for result in turn.tool_results:
                    messages.append(
                        {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
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
        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "messages": self._translate_history(system_prompt, history),
        }
        if tool_specs:
            request_kwargs["tools"] = self._translate_tool_specs(tool_specs)

        response = await self._client.chat.completions.create(**request_kwargs)

        message = response.choices[0].message
        # Stored verbatim (as a plain dict) so the exact same structure can
        # be re-sent as an `{"role": "assistant", ...}` message next round —
        # this mirrors how AnthropicProvider round-trips `response.content`.
        raw_message = message.model_dump(exclude_none=True)

        tool_calls = [
            ToolCallRequest(
                call_id=call.id,
                tool_name=call.function.name,
                arguments=json.loads(call.function.arguments or "{}"),
            )
            for call in (message.tool_calls or [])
        ]

        finish_reason = response.choices[0].finish_reason
        if finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif finish_reason == "stop":
            stop_reason = "end_turn"
        elif finish_reason == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "error"

        return LLMResponse(
            thought_text=message.content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_provider_message=raw_message,
        )
