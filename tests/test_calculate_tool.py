"""Tests for the calculate tool's safe math evaluation and its
adversarial-input hardening (no eval()/exec() escape hatch should exist).
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.calc.calculate_tool import register_calculate_tools
from tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_calculate_tools(registry)
    return registry


@pytest.mark.asyncio
async def test_basic_arithmetic(registry):
    assert await registry.dispatch("calculate", {"expression": "2 + 2"}) == "4"
    assert await registry.dispatch("calculate", {"expression": "(12 + 8) * 3"}) == "60"


@pytest.mark.asyncio
async def test_allowed_functions(registry):
    assert await registry.dispatch("calculate", {"expression": "sqrt(16)"}) == "4.0"
    assert await registry.dispatch("calculate", {"expression": "max(1, 5, 3)"}) == "5"


@pytest.mark.asyncio
async def test_division_by_zero_is_error_observation_not_crash(registry):
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("calculate", {"expression": "1 / 0"})


@pytest.mark.asyncio
async def test_overlong_expression_is_rejected(registry):
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("calculate", {"expression": "1+" * 150 + "1"})


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('dir')",
        "().__class__.__bases__[0].__subclasses__()",
        "open('secrets.txt').read()",
        "lambda: 1",
        "[x for x in range(10)]",
        "'a' + 'b'",
        "1 == 1",
        "os.system('dir')",
        "2 ** 999999999",  # pathological exponent — must be rejected, not computed
        "print('hi')",
    ],
)
@pytest.mark.asyncio
async def test_adversarial_inputs_are_rejected(registry, expression):
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("calculate", {"expression": expression})
