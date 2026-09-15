"""Unit tests for ToolRegistry: registration, dispatch, and error wrapping."""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="", input_schema={"type": "object", "properties": {}})


def test_register_duplicate_name_raises():
    registry = ToolRegistry()

    async def handler(args):
        return "ok"

    registry.register(_spec("dup"), handler)
    with pytest.raises(ValueError):
        registry.register(_spec("dup"), handler)


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises_tool_execution_error():
    registry = ToolRegistry()
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("missing", {})


@pytest.mark.asyncio
async def test_dispatch_wraps_handler_exceptions():
    registry = ToolRegistry()

    async def broken_handler(args):
        raise ValueError("boom")

    registry.register(_spec("broken"), broken_handler)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("broken", {})


@pytest.mark.asyncio
async def test_dispatch_returns_handler_result():
    registry = ToolRegistry()

    async def handler(args):
        return f"got {args['x']}"

    registry.register(_spec("echo"), handler)
    result = await registry.dispatch("echo", {"x": 5})
    assert result == "got 5"
