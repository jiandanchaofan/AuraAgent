"""Tests for the task tool, focused on the delete_task HITL gate — mirrors
tests/test_calendar_tool.py's structure and reuses the same
FakeConfirmationChannel test double.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_provider import TaskNotFoundError
from tools.tasks.task_tool import register_task_tools


@pytest.fixture
def provider(tmp_path):
    return LocalJSONTaskProvider(tmp_path / "tasks.json")


async def _create_task(registry: ToolRegistry, title: str = "Write report") -> str:
    result = await registry.dispatch("create_task", {"title": title})
    # "Created task 'Write report' (id=abcd1234)."
    return result.split("id=")[1].rstrip(").")


@pytest.mark.asyncio
async def test_create_and_list_round_trip(provider):
    registry = ToolRegistry()
    register_task_tools(registry, provider, FakeConfirmationChannel(decision=True))

    await _create_task(registry, title="Write report")
    result = await registry.dispatch("list_tasks", {})

    assert "Write report" in result


@pytest.mark.asyncio
async def test_complete_task_marks_done_and_excludes_from_open_listing(provider):
    registry = ToolRegistry()
    register_task_tools(registry, provider, FakeConfirmationChannel(decision=True))

    task_id = await _create_task(registry)
    result = await registry.dispatch("complete_task", {"task_id": task_id})
    assert "Completed" in result

    open_listing = await registry.dispatch("list_tasks", {"include_completed": False})
    assert task_id not in open_listing


@pytest.mark.asyncio
async def test_delete_always_asks_and_proceeds_on_approval(provider):
    confirmation = FakeConfirmationChannel(decision=True)
    registry = ToolRegistry()
    register_task_tools(registry, provider, confirmation)

    task_id = await _create_task(registry)
    result = await registry.dispatch("delete_task", {"task_id": task_id})

    assert "Deleted" in result
    assert len(confirmation.requests) == 1
    with pytest.raises(TaskNotFoundError):
        await provider.get_task(task_id)


@pytest.mark.asyncio
async def test_delete_declined_leaves_task_untouched(provider):
    confirmation = FakeConfirmationChannel(decision=False)
    registry = ToolRegistry()
    register_task_tools(registry, provider, confirmation)

    task_id = await _create_task(registry)
    result = await registry.dispatch("delete_task", {"task_id": task_id})

    assert "declined" in result.lower()
    fetched = await provider.get_task(task_id)
    assert fetched.title == "Write report"


@pytest.mark.asyncio
async def test_delete_missing_task_raises_tool_execution_error(provider):
    registry = ToolRegistry()
    register_task_tools(registry, provider, FakeConfirmationChannel(decision=True))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("delete_task", {"task_id": "nonexistent"})


@pytest.mark.asyncio
async def test_complete_missing_task_raises_tool_execution_error(provider):
    registry = ToolRegistry()
    register_task_tools(registry, provider, FakeConfirmationChannel(decision=True))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("complete_task", {"task_id": "nonexistent"})
