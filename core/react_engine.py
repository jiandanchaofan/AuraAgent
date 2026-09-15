"""AsyncReActEngine — the white-box Reason -> Act -> Observe loop.

This module intentionally imports nothing from providers/, tools/* concrete
tool modules, or confirmation/ — only the abstract LLMProvider and the
ToolRegistry's public dispatch surface. main.py is the only place that
wires concrete providers/tools together (composition root), which is what
keeps this engine reusable if AuraAgent later grows a FastAPI backend.
"""
from __future__ import annotations

from core.exceptions import MaxTurnsExceededError, ToolExecutionError
from core.logger import AuraLogger
from core.message_types import ConversationTurn, ToolResultInput
from providers.base import LLMProvider
from tools.registry import ToolRegistry


class AsyncReActEngine:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        logger: AuraLogger,
        system_prompt: str,
        max_turns: int = 15,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.logger = logger
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    async def run(self, user_input: str) -> str:
        """Run one ReAct task to completion: repeatedly call the LLM, dispatch
        any tools it asks for, feed the Observations back, until it produces a
        final answer (stop_reason != "tool_use") or max_turns is exceeded.
        """
        self.logger.log_user_input(user_input)
        history: list[ConversationTurn] = [ConversationTurn(role="user", text=user_input)]

        for turn_index in range(1, self.max_turns + 1):
            tool_specs = self.registry.get_tool_specs()
            self.logger.log_calling_llm(turn_index, self.provider.model_name, len(tool_specs))

            response = await self.provider.send(self.system_prompt, history, tool_specs)
            self.logger.log_thought(turn_index, response.thought_text)
            history.append(ConversationTurn(role="assistant", raw=response.raw_provider_message))

            if response.stop_reason != "tool_use":
                final_text = response.thought_text or ""
                self.logger.log_final_answer(final_text)
                return final_text

            tool_results: list[ToolResultInput] = []
            for call in response.tool_calls:
                self.logger.log_tool_call(turn_index, call.tool_name, call.arguments)
                try:
                    observation = await self.registry.dispatch(call.tool_name, call.arguments)
                    is_error = False
                except ToolExecutionError as exc:
                    observation, is_error = str(exc), True
                self.logger.log_observation(turn_index, call.tool_name, observation, is_error)
                tool_results.append(ToolResultInput(call_id=call.call_id, content=observation, is_error=is_error))

            history.append(ConversationTurn(role="user", tool_results=tool_results))

        raise MaxTurnsExceededError(self.max_turns)
