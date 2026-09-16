"""AsyncReActEngine — the white-box Reason -> Act -> Observe loop.

This module intentionally imports nothing from providers/, tools/* concrete
tool modules, confirmation/, or agents/ — only the abstract LLMProvider and
the ToolRegistry's public dispatch surface (structurally: anything with
get_tool_specs()/dispatch(), which tools.registry.ToolRegistry and
agents.scoped_tool_registry.ScopedToolRegistryView both satisfy). main.py
is the only place that wires concrete providers/tools/agents together
(composition root), which is what keeps this engine reusable if AuraAgent
later grows a FastAPI backend.

`agent_name` is an opaque display label used only for logging — the engine
has no awareness that Multi-Agent orchestration exists; it doesn't import
anything from agents/. Every AsyncReActEngine instance (Leader's or a
Worker's) is the same class, just constructed with a different name/prompt/
tool view.
"""
from __future__ import annotations

import asyncio

from core.exceptions import MaxTurnsExceededError, ToolExecutionError
from core.logger import AuraLogger
from core.message_types import ConversationTurn, ToolCallRequest, ToolResultInput
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
        agent_name: str = "root",
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.logger = logger
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.agent_name = agent_name

    async def run(self, user_input: str) -> str:
        """Run one ReAct task to completion: repeatedly call the LLM, dispatch
        any tools it asks for, feed the Observations back, until it produces a
        final answer (stop_reason != "tool_use") or max_turns is exceeded.
        """
        self.logger.log_user_input(user_input, agent_name=self.agent_name)
        history: list[ConversationTurn] = [ConversationTurn(role="user", text=user_input)]

        for turn_index in range(1, self.max_turns + 1):
            tool_specs = self.registry.get_tool_specs()
            self.logger.log_calling_llm(
                turn_index, self.provider.model_name, len(tool_specs), agent_name=self.agent_name
            )

            response = await self.provider.send(self.system_prompt, history, tool_specs)
            self.logger.log_thought(turn_index, response.thought_text, agent_name=self.agent_name)
            history.append(ConversationTurn(role="assistant", raw=response.raw_provider_message))

            if response.stop_reason != "tool_use":
                final_text = response.thought_text or ""
                self.logger.log_final_answer(final_text, agent_name=self.agent_name)
                return final_text

            # Tool calls within a single turn run concurrently (not a plain
            # sequential `for` loop) — this is a general ReAct-engine
            # capability (e.g. fetching 3 URLs at once benefits a
            # single-agent task too), not something bolted in just for
            # Multi-Agent's worker-as-tool delegation. `return_exceptions=True`
            # is required, not optional: without it, any unhandled exception
            # from one task (e.g. asyncio.CancelledError, a bug) would cancel
            # every sibling task and propagate, silently breaking the
            # existing guarantee that one failing tool call never corrupts
            # the others' results.
            raw_results = await asyncio.gather(
                *(self._dispatch_one(turn_index, call) for call in response.tool_calls),
                return_exceptions=True,
            )
            tool_results: list[ToolResultInput] = []
            for call, result in zip(response.tool_calls, raw_results):
                if isinstance(result, BaseException):
                    self.logger.log_error(
                        turn_index,
                        f"Unhandled exception dispatching '{call.tool_name}': {result}",
                        agent_name=self.agent_name,
                    )
                    result = ToolResultInput(
                        call_id=call.call_id, content=f"Unhandled error: {result}", is_error=True
                    )
                tool_results.append(result)

            history.append(ConversationTurn(role="user", tool_results=tool_results))

        raise MaxTurnsExceededError(self.max_turns)

    async def _dispatch_one(self, turn_index: int, call: ToolCallRequest) -> ToolResultInput:
        self.logger.log_tool_call(
            turn_index, call.tool_name, call.arguments, agent_name=self.agent_name, call_id=call.call_id
        )
        try:
            observation = await self.registry.dispatch(call.tool_name, call.arguments)
            is_error = False
        except ToolExecutionError as exc:
            observation, is_error = str(exc), True
        self.logger.log_observation(
            turn_index,
            call.tool_name,
            observation,
            is_error,
            agent_name=self.agent_name,
            call_id=call.call_id,
        )
        return ToolResultInput(call_id=call.call_id, content=observation, is_error=is_error)
