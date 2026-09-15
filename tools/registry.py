"""ToolRegistry — the single place native tools, MCP-adapted tools, and
dynamically loaded Skills all converge (all three call register() with the
same signature). core/react_engine.py depends only on get_tool_specs() and
dispatch(); it never imports a concrete tool module.
"""
from __future__ import annotations

from typing import Any

from core.exceptions import ToolExecutionError
from tools.base import RegisteredTool, ToolHandler, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get_tool_specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._tools.values()]

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        entry = self._tools.get(tool_name)
        if entry is None:
            raise ToolExecutionError(f"Unknown tool: '{tool_name}'")
        try:
            return await entry.handler(arguments)
        except ToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - tool handlers are untrusted, must never crash the loop
            raise ToolExecutionError(f"Tool '{tool_name}' raised {type(exc).__name__}: {exc}") from exc
