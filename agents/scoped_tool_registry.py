"""ScopedToolRegistryView — the per-Agent tool visibility + enforcement
boundary over the ONE shared ToolRegistry.

Exposes the exact same get_tool_specs()/dispatch() surface
AsyncReActEngine already depends on (structural typing — no shared base
class needed), so an AsyncReActEngine can't tell whether it's holding a
plain ToolRegistry or a scoped view of one.

Two responsibilities, not one:
1. Filtering — get_tool_specs() only returns tools whose name matches one
   of this agent's `allowed_patterns` (fnmatch globs against ToolSpec.name;
   an exact name is just a pattern with no wildcard). This shapes what the
   LLM is shown.
2. Enforcement — dispatch() re-checks the same allowlist before delegating
   to the underlying shared ToolRegistry. Filtering alone is not a
   boundary: it only changes what get_tool_specs() advertises, but nothing
   stops a hallucinating or adversarial tool_use call from naming an
   out-of-scope tool that genuinely exists in the shared registry. Without
   this check, an agent's `capabilities` list in config/agents.json would
   be advisory prompting, not a real restriction.

`extra_tools` holds synthetic tools that exist ONLY in this view and never
touch the shared ToolRegistry — this is how Leader-Worker delegation tools
(delegate_to_<worker>, see agents/delegate_tool.py) are scoped to the
Leader alone: Worker views are always built with extra_tools=None, so
delegate_to_* tools are structurally absent from anything a Worker can see
or call — not merely omitted by convention.
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from core.exceptions import ToolExecutionError
from tools.base import RegisteredTool, ToolSpec
from tools.registry import ToolRegistry


class ScopedToolRegistryView:
    def __init__(
        self,
        underlying: ToolRegistry,
        allowed_patterns: list[str],
        extra_tools: dict[str, RegisteredTool] | None = None,
    ) -> None:
        self._underlying = underlying
        self._allowed_patterns = allowed_patterns
        self._extra_tools = extra_tools or {}

    def _is_allowed(self, tool_name: str) -> bool:
        return any(fnmatch(tool_name, pattern) for pattern in self._allowed_patterns)

    def get_tool_specs(self) -> list[ToolSpec]:
        visible = [spec for spec in self._underlying.get_tool_specs() if self._is_allowed(spec.name)]
        visible.extend(entry.spec for entry in self._extra_tools.values())
        return visible

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name in self._extra_tools:
            return await self._extra_tools[tool_name].handler(arguments)
        if not self._is_allowed(tool_name):
            raise ToolExecutionError(f"Tool '{tool_name}' is not in this agent's capabilities")
        return await self._underlying.dispatch(tool_name, arguments)
