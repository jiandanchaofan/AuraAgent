"""Tool registry data shapes — deliberately provider-agnostic.

`ToolSpec.input_schema` is a plain JSON Schema object. This is exactly the
shape Anthropic's `tools=[{name, description, input_schema}]` API expects,
and it is also what OpenAI's `function.parameters` expects — so the same
ToolSpec list can be translated by any LLMProvider without tools/ needing
to know which provider is in use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# A handler receives the LLM's parsed tool-call arguments and returns the
# Observation text (or raises ToolExecutionError / lets an exception bubble
# up — ToolRegistry.dispatch() wraps unexpected exceptions either way).
ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler
