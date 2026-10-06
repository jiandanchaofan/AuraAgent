"""Unit tests for AnthropicProvider's pure translation logic (no network
calls) -- no test file existed for this provider before; added alongside
the fix for the same "assistant turn without `raw`" bug test_openai_provider.py
now also guards against (see providers/anthropic_provider.py)."""
from __future__ import annotations

from core.message_types import ConversationTurn, ToolResultInput
from providers.anthropic_provider import AnthropicProvider
from tools.base import ToolSpec


def _provider() -> AnthropicProvider:
    # Constructing AsyncAnthropic does not make a network call, so a fake key is fine.
    return AnthropicProvider(api_key="sk-ant-fake", model="claude-opus-5")


def test_translate_tool_specs_produces_input_schema_shape():
    provider = _provider()
    specs = [
        ToolSpec(
            name="search_notes",
            description="search",
            input_schema={"type": "object", "properties": {"keyword": {"type": "string"}}},
        )
    ]

    translated = provider._translate_tool_specs(specs)

    assert translated == [
        {"name": "search_notes", "description": "search", "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}}}
    ]


def test_translate_history_user_text_turn():
    provider = _provider()
    history = [ConversationTurn(role="user", text="hello")]
    messages = provider._translate_history(history)
    assert messages == [{"role": "user", "content": "hello"}]


def test_translate_history_assistant_turn_passes_raw_through_verbatim():
    provider = _provider()
    raw_content_blocks = [{"type": "text", "text": "hi there"}]
    history = [ConversationTurn(role="assistant", raw=raw_content_blocks)]

    messages = provider._translate_history(history)

    assert messages == [{"role": "assistant", "content": raw_content_blocks}]


def test_translate_history_assistant_turn_without_raw_falls_back_to_text():
    # Real bug this guards against: a chat resumed from persisted events
    # (gui/server.py's _history_from_events) reconstructs assistant turns
    # with `text` set and `raw` left as the default None -- sending
    # {"role": "assistant", "content": None} used to make the API reject
    # the whole request.
    provider = _provider()
    history = [ConversationTurn(role="assistant", text="Paris is the capital.")]

    messages = provider._translate_history(history)

    assert messages == [{"role": "assistant", "content": "Paris is the capital."}]


def test_translate_history_tool_results_turn_batches_into_one_user_message():
    provider = _provider()
    history = [
        ConversationTurn(
            role="user",
            tool_results=[
                ToolResultInput(call_id="call_1", content="42"),
                ToolResultInput(call_id="call_2", content="oops", is_error=True),
            ],
        )
    ]

    messages = provider._translate_history(history)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "42", "is_error": False},
                {"type": "tool_result", "tool_use_id": "call_2", "content": "oops", "is_error": True},
            ],
        }
    ]
