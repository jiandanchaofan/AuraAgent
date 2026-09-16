"""Tests for propose_capability_grant — structural pre-checks, the
approve/decline paths, and that grant_access (the same composed callback
propose_new_skill/propose_mcp_server use) is what actually widens access.
"""
from __future__ import annotations

import pytest

from agents.scoped_tool_registry import ScopedToolRegistryView
from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.propose_capability_grant_tool import register_propose_capability_grant_tool


async def _handler(args):
    return "ok"


def _spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(name=name, description=description, input_schema={"type": "object", "properties": {}})


def _setup(decision: bool = True):
    registry = ToolRegistry()
    registry.register(_spec("market_new_products", "Research new products"), _handler)
    view = ScopedToolRegistryView(registry, ["calculate"])
    confirmation = FakeConfirmationChannel(decision=decision)
    granted: list[str] = []

    async def grant_access(tool_name: str) -> None:
        view.add_allowed_pattern(tool_name)
        granted.append(tool_name)

    register_propose_capability_grant_tool(registry, view, confirmation, grant_access=grant_access)
    return registry, view, confirmation, granted


@pytest.mark.asyncio
async def test_approval_grants_access():
    registry, view, confirmation, granted = _setup(decision=True)

    result = await registry.dispatch("propose_capability_grant", {"tool_name": "market_new_products", "reason": "need it"})

    assert "Granted" in result
    assert granted == ["market_new_products"]
    assert view.is_allowed("market_new_products") is True


@pytest.mark.asyncio
async def test_decline_makes_no_changes():
    registry, view, confirmation, granted = _setup(decision=False)

    result = await registry.dispatch("propose_capability_grant", {"tool_name": "market_new_products"})

    assert "declined" in result.lower()
    assert granted == []
    assert view.is_allowed("market_new_products") is False


@pytest.mark.asyncio
async def test_unknown_tool_name_rejected_before_bothering_the_human():
    registry, view, confirmation, granted = _setup(decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_capability_grant", {"tool_name": "nonexistent_tool"})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_already_allowed_tool_rejected_before_bothering_the_human():
    registry, view, confirmation, granted = _setup(decision=True)
    view.add_allowed_pattern("market_new_products")

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_capability_grant", {"tool_name": "market_new_products"})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_proposal_text_includes_description_and_no_new_code_note():
    registry, view, confirmation, granted = _setup(decision=False)

    await registry.dispatch("propose_capability_grant", {"tool_name": "market_new_products", "reason": "need it"})

    reason = confirmation.requests[0].reason
    assert "Research new products" in reason
    assert "does NOT install any new code" in reason
    assert confirmation.requests[0].risk_level == "capability_grant"
