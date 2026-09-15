"""Test doubles for LLMProvider and ConfirmationChannel, used to drive
core/react_engine.py deterministically without hitting a real API.
"""
from __future__ import annotations

from core.message_types import ConversationTurn, LLMResponse


class FakeLLMProvider:
    """Replays a fixed sequence of LLMResponse objects, one per call to send().

    Also records the `history` it was called with each time, so tests can
    assert on how the engine folded tool_results back into the conversation.
    """

    def __init__(self, scripted_responses: list[LLMResponse]) -> None:
        self._responses = list(scripted_responses)
        self.model_name = "fake-model"
        self.sent_histories: list[list[ConversationTurn]] = []

    async def send(self, system_prompt: str, history: list[ConversationTurn], tool_specs) -> LLMResponse:
        self.sent_histories.append(list(history))
        if not self._responses:
            raise AssertionError("FakeLLMProvider ran out of scripted responses")
        return self._responses.pop(0)


class FakeConfirmationChannel:
    """Always returns a fixed decision; records every request it saw."""

    def __init__(self, decision: bool) -> None:
        self.decision = decision
        self.requests: list[object] = []

    async def confirm(self, request) -> bool:
        self.requests.append(request)
        return self.decision
