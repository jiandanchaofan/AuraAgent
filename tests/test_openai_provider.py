"""Unit tests for OpenAIProvider's pure translation logic (no network
calls) — the same class serves OpenAI and any OpenAI-compatible endpoint
(DeepSeek, etc.), so getting this translation right is what makes that
work; see providers/openai_provider.py for the manual verification run
against DeepSeek.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.exceptions import LLMOutputTruncatedError
from core.message_types import ConversationTurn, ToolResultInput
from providers.openai_provider import OpenAIProvider
from tools.base import ToolSpec


def _provider() -> OpenAIProvider:
    # Constructing AsyncOpenAI does not make a network call, so a fake key is fine.
    return OpenAIProvider(api_key="sk-fake", model="deepseek-chat", base_url="https://api.deepseek.com")


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {"role": "assistant", "content": self.content}


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, choices: list[_FakeChoice]) -> None:
        self.choices = choices


@pytest.mark.asyncio
async def test_send_raises_a_clear_error_when_a_tool_calls_arguments_are_truncated():
    # Real bug this guards against: a large tool call (e.g. create_pptx for
    # a many-slide deck) can get cut off mid-JSON when the model hits
    # max_tokens -- json.loads() on the resulting fragment used to raise a
    # bare, uninformative json.JSONDecodeError straight out of send().
    provider = _provider()
    truncated_call = _FakeToolCall("call_1", "create_pptx", '{"path": "demo.pptx", "title": "Some')
    response = _FakeResponse(
        [_FakeChoice(_FakeMessage(None, [truncated_call]), finish_reason="length")]
    )
    provider._client.chat.completions.create = AsyncMock(return_value=response)

    with pytest.raises(LLMOutputTruncatedError, match="create_pptx") as exc_info:
        await provider.send("sys", [], [])
    assert "max_tokens" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_reports_unparseable_json_distinctly_from_truncation():
    provider = _provider()
    bad_call = _FakeToolCall("call_1", "create_pptx", "not json at all")
    response = _FakeResponse([_FakeChoice(_FakeMessage(None, [bad_call]), finish_reason="stop")])
    provider._client.chat.completions.create = AsyncMock(return_value=response)

    with pytest.raises(LLMOutputTruncatedError, match="could not be parsed"):
        await provider.send("sys", [], [])


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


def test_translate_history_assistant_turn_without_raw_falls_back_to_text():
    # Real bug this guards against: a chat resumed from persisted events
    # (gui/server.py's _history_from_events) reconstructs assistant turns
    # with `text` set and `raw` left as the default None -- appending
    # `turn.raw` unconditionally used to put a literal `null` in the
    # `messages` array, which the API rejects the whole request over
    # ("invalid type: null, expected ... ChatCompletionRequestMessage").
    provider = _provider()
    history = [ConversationTurn(role="assistant", text="Paris is the capital.")]

    messages = provider._translate_history("sys", history)

    assert messages[1] == {"role": "assistant", "content": "Paris is the capital."}


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
