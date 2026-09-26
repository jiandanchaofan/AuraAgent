"""Test doubles for LLMProvider and ConfirmationChannel, used to drive
core/react_engine.py deterministically without hitting a real API.
"""
from __future__ import annotations

from pathlib import Path

from core.logger import AuraLogger, JSONLSink
from core.message_types import ConversationTurn, LLMResponse


def make_test_logger(tmp_path: Path) -> AuraLogger:
    """AuraLogger([JSONLSink(tmp_path)]) — the common "just needs a working
    logger, don't care about terminal output" test setup, factored out
    since AuraLogger's constructor takes a list of LogSinks (see
    core/logger.py) rather than a bare directory path."""
    return AuraLogger([JSONLSink(tmp_path)])


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
    """Always returns a fixed decision/answer; records every request it saw."""

    def __init__(self, decision: bool = True, answer: str = "") -> None:
        self.decision = decision
        self.answer = answer
        self.requests: list[object] = []
        self.questions_asked: list[str] = []

    async def confirm(self, request) -> bool:
        self.requests.append(request)
        return self.decision

    async def ask_open_question(self, prompt: str) -> str:
        self.questions_asked.append(prompt)
        return self.answer
