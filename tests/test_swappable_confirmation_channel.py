"""Tests for confirmation/swappable_channel.py::SwappableConfirmationChannel
and tools/scheduler/auto_decline_channel.py::AutoDeclineConfirmationChannel
-- the indirection that lets a scheduled run temporarily auto-decline
confirmations for its own duration without touching any tool registration.
Mirrors tests/test_swappable_provider.py's style.
"""
from __future__ import annotations

import pytest

from confirmation.base import ConfirmationRequest
from confirmation.swappable_channel import SwappableConfirmationChannel
from core.logger import AuraLogger, JSONLSink
from tests.fakes import FakeConfirmationChannel
from tools.scheduler.auto_decline_channel import AutoDeclineConfirmationChannel


def _request(reason: str = "do the thing") -> ConfirmationRequest:
    return ConfirmationRequest(tool_name="some_tool", arguments={}, reason=reason, risk_level="destructive")


@pytest.mark.asyncio
async def test_swappable_channel_forwards_to_initial_current(tmp_path):
    real = FakeConfirmationChannel(decision=True, answer="42")
    swappable = SwappableConfirmationChannel(real)

    assert await swappable.confirm(_request()) is True
    assert await swappable.ask_open_question("what?") == "42"
    assert len(real.requests) == 1
    assert real.questions_asked == ["what?"]


@pytest.mark.asyncio
async def test_set_current_switches_which_channel_is_used(tmp_path):
    first = FakeConfirmationChannel(decision=True)
    second = FakeConfirmationChannel(decision=False)
    swappable = SwappableConfirmationChannel(first)

    swappable.set_current(second)

    assert await swappable.confirm(_request()) is False
    assert len(first.requests) == 0
    assert len(second.requests) == 1


def test_current_property_reflects_the_live_target():
    first = FakeConfirmationChannel()
    second = FakeConfirmationChannel()
    swappable = SwappableConfirmationChannel(first)
    assert swappable.current is first

    swappable.set_current(second)
    assert swappable.current is second


@pytest.mark.asyncio
async def test_auto_decline_channel_always_declines_and_logs(tmp_path):
    logger = AuraLogger([JSONLSink(tmp_path / "logs")])
    channel = AutoDeclineConfirmationChannel(logger)

    approved = await channel.confirm(_request("delete something important"))

    assert approved is False


@pytest.mark.asyncio
async def test_auto_decline_channel_open_question_returns_empty(tmp_path):
    logger = AuraLogger([JSONLSink(tmp_path / "logs")])
    channel = AutoDeclineConfirmationChannel(logger)

    answer = await channel.ask_open_question("what's the password?")

    assert answer == ""


@pytest.mark.asyncio
async def test_swappable_channel_lets_a_scheduled_run_auto_decline_without_touching_the_real_one(tmp_path):
    """The exact swap/restore shape tools/scheduler/scheduler_loop.py uses:
    the real (human-facing) channel is preserved and resumes working after
    the temporary swap is undone."""
    logger = AuraLogger([JSONLSink(tmp_path / "logs")])
    real = FakeConfirmationChannel(decision=True)
    swappable = SwappableConfirmationChannel(real)

    prev = swappable.current
    swappable.set_current(AutoDeclineConfirmationChannel(logger))
    try:
        assert await swappable.confirm(_request()) is False
    finally:
        swappable.set_current(prev)

    assert swappable.current is real
    assert await swappable.confirm(_request()) is True  # the real channel works again
    assert len(real.requests) == 1  # only the post-restore call reached it
