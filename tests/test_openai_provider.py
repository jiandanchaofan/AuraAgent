"""Unit tests for OpenAIProvider's pure translation logic (no network
calls) — the same class serves OpenAI and any OpenAI-compatible endpoint
(DeepSeek, etc.), so getting this translation right is what makes that
work; see providers/openai_provider.py for the manual verification run
against DeepSeek.
"""
from __future__ import annotations

from core.message_types import ConversationTurn, ToolResultInput
from providers.openai_provider import OpenAIProvider
from tools.base import ToolSpec


def _provider() -> OpenAIProvider:
    # Constructing AsyncOpenAI does not make a network call, so a fake key is fine.
    return OpenAIProvider(api_key="sk-fake", model="deepseek-chat", base_url="https://api.deepseek.com")


def test_translate_tool_specs_produces_function_calling_shape():
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
        {
            "type": "function",
            "function": {
                "name": "search_notes",
                "description": "search",
                "parameters": {"type": "object", "properties": {"keyword": {"type": "string"}}},
            },
        }
    ]


def test_translate_history_includes_system_prompt_first():
    provider = _provider()
    messages = provider._translate_history("be helpful", [])
    assert messages == [{"role": "system", "content": "be helpful"}]


def test_translate_history_user_text_turn():
    provider = _provider()
    history = [ConversationTurn(role="user", text="hello")]
    messages = provider._translate_history("sys", history)
    assert messages[1] == {"role": "user", "content": "hello"}


def test_translate_history_assistant_turn_passes_raw_through_verbatim():
    provider = _provider()
    raw_assistant_message = {"role": "assistant", "content": "hi there", "tool_calls": None}
    history = [ConversationTurn(role="assistant", raw=raw_assistant_message)]

    messages = provider._translate_history("sys", history)

    assert messages[1] is raw_assistant_message


def test_translate_history_tool_results_turn_expands_to_one_message_per_call():
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

    messages = provider._translate_history("sys", history)

    assert messages[1:] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "42"},
        {"role": "tool", "tool_call_id": "call_2", "content": "oops"},
    ]
