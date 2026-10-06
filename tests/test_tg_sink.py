"""Tests for tg_bot/sink.py::TelegramLogSink -- the forwarding filter
(only final_answer/error/schedule_result become messages, tool_call names
fold into a "🔧 used: ..." suffix instead of one message each) and the
_chunk_text() splitting logic for Telegram's ~4096-char message cap.
"""
from __future__ import annotations

import asyncio

import pytest

from tg_bot.sink import TelegramLogSink, _chunk_text


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text):
        self.sent.append({"chat_id": chat_id, "text": text})


def _event(event_type: str, payload: dict) -> dict:
    return {"ts": "t", "event_type": event_type, "payload": payload}


def test_write_never_blocks_and_only_forwards_known_event_types():
    sink = TelegramLogSink()
    sink.write(_event("thought", {"text": "thinking..."}))
    sink.write(_event("calling_llm", {}))
    sink.write(_event("observation", {"result": "x"}))
    sink.write(_event("final_answer", {"text": "done"}))

    assert sink._queue.qsize() == 1
    queued = sink._queue.get_nowait()
    assert queued["event_type"] == "final_answer"


def test_tool_call_events_are_accumulated_not_enqueued():
    sink = TelegramLogSink()
    sink.write(_event("tool_call", {"tool_name": "write_file"}))
    sink.write(_event("tool_call", {"tool_name": "read_file"}))

    assert sink._queue.qsize() == 0
    assert sink._tool_calls == ["write_file", "read_file"]


def test_render_appends_tool_summary_and_resets_accumulator():
    sink = TelegramLogSink()
    sink.write(_event("tool_call", {"tool_name": "write_file"}))
    sink.write(_event("tool_call", {"tool_name": "read_file"}))

    first = sink._render({"event_type": "final_answer", "payload": {"text": "done"}})
    assert first == "done\n\n🔧 used: write_file, read_file"

    second = sink._render({"event_type": "final_answer", "payload": {"text": "done again"}})
    assert second == "done again"  # accumulator was reset, no trailing summary


def test_render_error_and_schedule_result():
    sink = TelegramLogSink()
    assert sink._render({"event_type": "error", "payload": {"message": "boom"}}) == "⚠️ boom"
    assert (
        sink._render({"event_type": "schedule_result", "payload": {"task": "daily digest", "summary": "all good"}})
        == "✅ Scheduled task finished: daily digest\nall good"
    )


@pytest.mark.asyncio
async def test_drain_sends_rendered_chunks_to_the_configured_chat():
    sink = TelegramLogSink()
    bot = _FakeBot()
    drain_task = asyncio.create_task(sink.drain(bot, 123))

    sink.write(_event("final_answer", {"text": "hello"}))
    await asyncio.sleep(0.01)

    assert bot.sent == [{"chat_id": 123, "text": "hello"}]
    drain_task.cancel()


def test_chunk_text_under_limit_returns_one_chunk():
    assert _chunk_text("short", limit=100) == ["short"]


def test_chunk_text_splits_on_paragraph_boundary_without_losing_content():
    text = ("a" * 50) + "\n\n" + ("b" * 50)
    chunks = _chunk_text(text, limit=60)

    assert len(chunks) == 2
    assert "".join(chunks).replace("\n\n", "") == ("a" * 50) + ("b" * 50)
    assert chunks[0] == "a" * 50


def test_chunk_text_hard_splits_when_no_paragraph_boundary_exists():
    text = "a" * 150
    chunks = _chunk_text(text, limit=60)

    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks) == text
