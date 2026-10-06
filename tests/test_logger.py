"""Tests for core/logger.py's LogSink abstraction: that AuraLogger fans
every event out to all registered sinks with a consistent shape, and that
JSONLSink persists exactly what it's given. TerminalSink's rich rendering
itself isn't asserted on here (it's cosmetic, verified by eyeballing real
runs, same as before this refactor) — these tests are about the
event-fanout contract the GUI backend (a future WebSocketSink) depends on.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from core.logger import AuraLogger, JSONLSink, LogSink


class _RecordingSink(LogSink):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_event_has_the_expected_top_level_shape():
    sink = _RecordingSink()
    logger = AuraLogger([sink])

    logger.log_user_input("hello", agent_name="orchestrator")

    assert len(sink.events) == 1
    event = sink.events[0]
    assert set(event.keys()) == {"ts", "turn", "agent_name", "event_type", "payload"}
    assert event["agent_name"] == "orchestrator"
    assert event["event_type"] == "user_input"
    assert event["payload"] == {"text": "hello"}


def test_every_log_method_fans_out_to_every_sink():
    sink_a, sink_b = _RecordingSink(), _RecordingSink()
    logger = AuraLogger([sink_a, sink_b])

    logger.log_user_input("hi")
    logger.log_calling_llm(1, "deepseek-chat", 5)
    logger.log_thought(1, "thinking...")
    logger.log_tool_call(1, "calculate", {"expr": "1+1"}, call_id="c1")
    logger.log_observation(1, "calculate", "2", is_error=False, call_id="c1")
    logger.log_confirmation(1, "delete this?", True)
    logger.log_final_answer("done")
    logger.log_error(1, "boom")
    logger.log_schedule_result("sched1", "daily insight", "the result")

    assert len(sink_a.events) == len(sink_b.events) == 9
    assert [e["event_type"] for e in sink_a.events] == [
        "user_input", "calling_llm", "thought", "tool_call", "observation", "confirmation", "final_answer", "error",
        "schedule_result",
    ]


def test_log_schedule_result_payload_shape():
    sink = _RecordingSink()
    logger = AuraLogger([sink])

    logger.log_schedule_result("sched1", "daily insight", "the result")

    assert sink.events[0]["payload"] == {"schedule_id": "sched1", "task": "daily insight", "summary": "the result"}
    assert sink.events[0]["agent_name"] == "scheduler"


def test_log_thought_skips_all_sinks_when_empty():
    sink = _RecordingSink()
    logger = AuraLogger([sink])

    logger.log_thought(1, None)
    logger.log_thought(1, "")

    assert sink.events == []


def test_log_calling_llm_now_reaches_jsonl_sinks_too(tmp_path):
    """Prior to the LogSink refactor, log_calling_llm only ever printed to
    the terminal and never reached the JSONL file at all -- an
    inconsistency with every other log_* method. Fixed as part of this
    refactor: it's now just another event, reaching every sink uniformly."""
    sink = JSONLSink(tmp_path)
    logger = AuraLogger([sink])

    logger.log_calling_llm(2, "claude-opus-5", 10, agent_name="researcher")

    lines = sink._log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_type"] == "calling_llm"
    assert record["payload"] == {"model": "claude-opus-5", "tool_count": 10}


def test_jsonl_sink_appends_one_line_per_event(tmp_path):
    sink = JSONLSink(tmp_path)
    logger = AuraLogger([sink])

    logger.log_user_input("first")
    logger.log_final_answer("second")

    lines = sink._log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"] == {"text": "first"}
    assert json.loads(lines[1])["payload"] == {"text": "second"}


def test_jsonl_sink_creates_a_timestamped_file_under_log_dir(tmp_path):
    sink = JSONLSink(tmp_path)
    assert sink._log_path.parent == tmp_path
    assert sink._log_path.name.startswith("session-") and sink._log_path.name.endswith(".jsonl")


@pytest.mark.asyncio
async def test_agent_name_and_call_id_survive_concurrent_dispatch_fanout(tmp_path):
    """Regression-style check that the fields Multi-Agent's concurrent
    dispatch relies on for reconstructing an interleaved trace (agent_name,
    call_id) are preserved end to end through the new sink plumbing."""
    sink = _RecordingSink()
    logger = AuraLogger([sink])

    logger.log_tool_call(1, "fetch_url", {"url": "https://example.com"}, agent_name="researcher", call_id="abc123")

    event = sink.events[0]
    assert event["agent_name"] == "researcher"
    assert event["payload"]["call_id"] == "abc123"
