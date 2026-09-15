"""LLMProvider abstraction — the seam between the ReAct engine and any
specific model vendor's API.

The engine only ever speaks in terms of ConversationTurn, ToolSpec, and
LLMResponse (see core/message_types.py and tools/base.py). Each concrete
provider translates to/from its own wire format entirely inside its own
module (Anthropic's tool_use/tool_result content blocks today; an
OpenAI-style function_call shape could be added later — see
providers/openai_provider.py), so adding a provider never touches
core/ or tools/.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.message_types import ConversationTurn, LLMResponse
    from tools.base import ToolSpec


class LLMProvider(ABC):
    #: Human-readable model identifier, used only for logging (core/logger.py).
    model_name: str

    @abstractmethod
    async def send(
        self,
        system_prompt: str,
        history: list["ConversationTurn"],
        tool_specs: list["ToolSpec"],
    ) -> "LLMResponse":
        """One round-trip: send the full conversation state, get back the next
        assistant turn. `history` already includes any tool_results turn from
        the previous round (see core/react_engine.py) — providers are
        stateless across calls and rebuild their native message list from
        `history` every time, which keeps a provider instance safe to reuse
        across independent AsyncReActEngine.run() calls.
        """
        ...
