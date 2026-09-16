"""Tests for ScopedToolRegistryView — filtering AND enforcement (not
just what get_tool_specs() advertises), plus the extra_tools mechanism
that keeps delegate_to_<worker> tools structurally absent from any view
that wasn't given them.
"""
from __future__ import annotations

import pytest

from agents.scoped_tool_registry import ScopedToolRegistryView
from core.exceptions import ToolExecutionError
from tools.base import RegisteredTool, ToolSpec
from tools.registry import ToolRegistry


async def _handler(args):
    return "ok"


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="", input_schema={"type": "object", "properties": {}})


@pytest.fixture
def shared_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_spec("create_task"), _handler)
    registry.register(_spec("list_tasks"), _handler)
    registry.register(_spec("fetch_url"), _handler)
    return registry


def test_get_tool_specs_filters_by_pattern(shared_registry):
    view = ScopedToolRegistryView(shared_registry, ["*task*"])
    names = {s.name for s in view.get_tool_specs()}
    assert names == {"create_task", "list_tasks"}


def test_exact_name_pattern_matches_only_that_tool(shared_registry):
    view = ScopedToolRegistryView(shared_registry, ["fetch_url"])
    names = {s.name for s in view.get_tool_specs()}
    assert names == {"fetch_url"}


def test_wildcard_star_matches_everything(shared_registry):
    view = ScopedToolRegistryView(shared_registry, ["*"])
    names = {s.name for s in view.get_tool_specs()}
    assert names == {"create_task", "list_tasks", "fetch_url"}


@pytest.mark.asyncio
async def test_dispatch_allows_in_scope_tool(shared_registry):
    view = ScopedToolRegistryView(shared_registry, ["*task*"])
    result = await view.dispatch("create_task", {})
    assert result == "ok"


@pytest.mark.asyncio
async def test_dispatch_rejects_out_of_scope_tool_even_though_it_exists_in_shared_registry(shared_registry):
    """The critical enforcement test: fetch_url is a real tool in the
    shared registry, but this view's capabilities don't include it —
    dispatch() must reject it, not just omit it from get_tool_specs()."""
    view = ScopedToolRegistryView(shared_registry, ["*task*"])
    with pytest.raises(ToolExecutionError):
        await view.dispatch("fetch_url", {})


@pytest.mark.asyncio
async def test_extra_tools_are_visible_and_dispatchable(shared_registry):
    async def delegate_handler(args):
        return f"delegated: {args['task']}"

    extra = {
        "delegate_to_researcher": RegisteredTool(
            spec=_spec("delegate_to_researcher"), handler=delegate_handler
        )
    }
    view = ScopedToolRegistryView(shared_registry, ["*task*"], extra_tools=extra)

    names = {s.name for s in view.get_tool_specs()}
    assert "delegate_to_researcher" in names

    result = await view.dispatch("delegate_to_researcher", {"task": "look something up"})
    assert result == "delegated: look something up"


def test_view_without_extra_tools_never_exposes_delegate_tools(shared_registry):
    """Structural guarantee behind "Workers can't delegate to each other":
    a view built with extra_tools=None simply has no delegate_to_* entries
    to match against, regardless of its capabilities patterns."""
    view = ScopedToolRegistryView(shared_registry, ["*"])
    names = {s.name for s in view.get_tool_specs()}
    assert not any(name.startswith("delegate_to_") for name in names)


@pytest.mark.asyncio
async def test_shared_registry_state_is_visible_across_views(shared_registry):
    """Two different scoped views over the SAME underlying registry see
    each other's effects — there is exactly one source of truth, per the
    "no duplicate registration" design."""
    scheduler_view = ScopedToolRegistryView(shared_registry, ["*task*"])
    admin_view = ScopedToolRegistryView(shared_registry, ["*"])

    await scheduler_view.dispatch("create_task", {})
    # No separate state to go stale — dispatch() always routes through the
    # one shared ToolRegistry, so this is really just confirming both views
    # reach the same handler, not testing any caching/sync mechanism.
    result = await admin_view.dispatch("create_task", {})
    assert result == "ok"
