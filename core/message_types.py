"""Provider-neutral data structures that flow through the ReAct engine.

These types exist so core/react_engine.py never has to know about any
specific LLM vendor's wire format (Anthropic's tool_use/tool_result
content blocks, an OpenAI-style function_call shape, ...). Each concrete
LLMProvider translates to/from these at its own boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCallRequest:
    """One tool invocation the LLM asked for, in provider-neutral form."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolResultInput:
    """The Observation for one prior tool call, ready to send back to the LLM."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ConversationTurn:
    """One entry in the running conversation history that AsyncReActEngine
    accumulates and passes to LLMProvider.send() on every round-trip.

    Exactly one of the three optional fields is meaningful per role:
      - role="user",      text=...          -> the original user request
      - role="user",      tool_results=...  -> Observations sent back after a tool_use turn
      - role="assistant", raw=...           -> the provider's own content blocks,
                                                kept verbatim so the same provider
                                                can round-trip its native format
    """

    role: Literal["user", "assistant"]
    text: str | None = None
    tool_results: list[ToolResultInput] | None = None
    raw: Any = None


@dataclass
class LLMResponse:
    """One full assistant turn, normalized across providers."""

    thought_text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: Literal["tool_use", "end_turn", "max_tokens", "error"] = "end_turn"
    raw_provider_message: Any = None
