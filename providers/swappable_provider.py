"""SwappableProvider — an LLMProvider that forwards every call to whichever
concrete provider it currently holds, and can be swapped at runtime.

Every AsyncReActEngine (Leader's, every Worker's, and any built later by
propose_new_agent / the /agents add CLI command via agents/agent_builder.py)
is constructed with a reference to the SAME SwappableProvider instance
instead of a concrete AnthropicProvider/OpenAIProvider directly. That
single level of indirection is what lets the /config use CLI command
(cli/commands.py) switch every engine in the running system over to a new
provider/model at once, by calling set_current() here — without this,
each engine would be holding its own frozen reference to the provider
object main.py originally constructed, and switching would need to reach
into and mutate every engine individually (including ones not built yet).

core/react_engine.py needs nothing new from this: it only ever accesses
`self.provider.model_name` (an attribute) and `await self.provider.send(...)`,
both of which this class provides by delegating to `_current`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from providers.base import LLMProvider

if TYPE_CHECKING:
    from core.message_types import ConversationTurn, LLMResponse
    from tools.base import ToolSpec


class SwappableProvider(LLMProvider):
    def __init__(self, initial: LLMProvider, initial_name: str) -> None:
        self._current = initial
        #: "anthropic" | "openai" — not part of the LLMProvider interface
        #: itself, tracked here purely so /config can report which one is
        #: active without needing to inspect the concrete provider's type.
        self.provider_name = initial_name

    @property
    def model_name(self) -> str:
        return self._current.model_name

    def set_current(self, new_provider: LLMProvider, name: str) -> None:
        self._current = new_provider
        self.provider_name = name

    async def send(
        self,
        system_prompt: str,
        history: list["ConversationTurn"],
        tool_specs: list["ToolSpec"],
    ) -> "LLMResponse":
        return await self._current.send(system_prompt, history, tool_specs)
