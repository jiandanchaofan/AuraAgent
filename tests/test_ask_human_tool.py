"""Tests for the ask_human tool, plus a regression check that both
ConfirmationChannel implementations (TerminalConfirmationChannel and
FakeConfirmationChannel) still satisfy the ABC after it gained
ask_open_question().
"""
from __future__ import annotations

import pytest

from confirmation.terminal_channel import TerminalConfirmationChannel
from tests.fakes import FakeConfirmationChannel
from tools.human.ask_human_tool import register_ask_human_tools
from tools.registry import ToolRegistry


def test_terminal_confirmation_channel_still_instantiates():
    # Would raise TypeError if TerminalConfirmationChannel hadn't implemented
    # the new abstract method ask_open_question().
    TerminalConfirmationChannel()


@pytest.mark.asyncio
async def test_ask_human_returns_the_answer():
    confirmation = FakeConfirmationChannel(answer="next Tuesday")
    registry = ToolRegistry()
    register_ask_human_tools(registry, confirmation)

    result = await registry.dispatch("ask_human", {"question": "When is the deadline?"})

    assert result == "Human answered: next Tuesday"
    assert confirmation.questions_asked == ["When is the deadline?"]


@pytest.mark.asyncio
async def test_ask_human_empty_answer_returns_fallback_text():
    confirmation = FakeConfirmationChannel(answer="")
    registry = ToolRegistry()
    register_ask_human_tools(registry, confirmation)

    result = await registry.dispatch("ask_human", {"question": "Anything else?"})

    assert result == "Human provided no answer (empty response)."
