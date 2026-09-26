"""Tests for TerminalConfirmationChannel: the Y/N prompt itself (already
implicitly covered elsewhere via FakeConfirmationChannel in most tool
tests), and specifically the log_confirmation wiring added alongside
core/logger.py's LogSink refactor -- confirm() must report its decision
to the logger (this was previously a dead code path, log_confirmation()
existed but nothing ever called it), while ask_open_question() must NOT
log anything at all, since it's also used to collect secret values
(propose_mcp_server's env var prompts).
"""
from __future__ import annotations

import pytest

from confirmation.base import ConfirmationRequest
from confirmation.terminal_channel import TerminalConfirmationChannel
from core.logger import AuraLogger, LogSink


class _RecordingSink(LogSink):
    def __init__(self) -> None:
        self.events = []

    def write(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_confirm_yes_reports_decision_to_the_logger(monkeypatch):
    sink = _RecordingSink()
    channel = TerminalConfirmationChannel(AuraLogger([sink]))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    approved = await channel.confirm(ConfirmationRequest(tool_name="delete_file", arguments={}, reason="delete it?"))

    assert approved is True
    assert len(sink.events) == 1
    assert sink.events[0]["event_type"] == "confirmation"
    assert sink.events[0]["payload"] == {"prompt": "delete it?", "decision": True}


@pytest.mark.asyncio
async def test_confirm_no_reports_decision_to_the_logger(monkeypatch):
    sink = _RecordingSink()
    channel = TerminalConfirmationChannel(AuraLogger([sink]))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    approved = await channel.confirm(ConfirmationRequest(tool_name="delete_file", arguments={}, reason="delete it?"))

    assert approved is False
    assert sink.events[0]["payload"]["decision"] is False


@pytest.mark.asyncio
async def test_ask_open_question_never_logs_anything(monkeypatch):
    """Real security requirement: this channel is also how propose_mcp_server
    collects secret env-var values from a human (see
    tools/self_extend/propose_mcp_tool.py) -- those must never reach
    logs/session-*.jsonl or a GUI event stream."""
    sink = _RecordingSink()
    channel = TerminalConfirmationChannel(AuraLogger([sink]))
    monkeypatch.setattr("builtins.input", lambda prompt="": "super-secret-value")

    answer = await channel.ask_open_question("Enter the API key:")

    assert answer == "super-secret-value"
    assert sink.events == []
