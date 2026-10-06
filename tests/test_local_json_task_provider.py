"""Tests for tools/tasks/local_json_task_provider.py -- the local/default
TaskProvider backend. No test file existed for this one directly before
(only tests/test_task_tool.py's tool-level tests); these cover the
update_task/set_task_done additions specifically, since the UNSET-sentinel
"field not passed vs. explicitly cleared" distinction is the one genuinely
fiddly piece of correctness here.
"""
from __future__ import annotations

import pytest

from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_provider import TaskNotFoundError


def _provider(tmp_path) -> LocalJSONTaskProvider:
    return LocalJSONTaskProvider(tmp_path / "tasks.json")


@pytest.mark.asyncio
async def test_update_task_title_only_leaves_notes_and_due_untouched(tmp_path):
    provider = _provider(tmp_path)
    created = await provider.create_task("Old title", notes="keep me", due="2026-03-05")

    updated = await provider.update_task(created.id, title="New title")

    assert updated.title == "New title"
    assert updated.notes == "keep me"
    assert updated.due == "2026-03-05"


@pytest.mark.asyncio
async def test_update_task_can_clear_notes_and_due_explicitly(tmp_path):
    provider = _provider(tmp_path)
    created = await provider.create_task("Task", notes="some notes", due="2026-03-05")

    updated = await provider.update_task(created.id, notes=None, due=None)

    assert updated.notes is None
    assert updated.due is None
    assert updated.title == "Task"  # untouched


@pytest.mark.asyncio
async def test_update_task_unknown_id_raises(tmp_path):
    provider = _provider(tmp_path)

    with pytest.raises(TaskNotFoundError):
        await provider.update_task("nonexistent", title="x")


@pytest.mark.asyncio
async def test_set_task_done_toggles_both_directions_and_clears_completed_at_when_unchecked(tmp_path):
    provider = _provider(tmp_path)
    created = await provider.create_task("Task")

    done = await provider.set_task_done(created.id, True)
    assert done.done is True
    assert done.completed_at is not None

    not_done = await provider.set_task_done(created.id, False)
    assert not_done.done is False
    assert not_done.completed_at is None


@pytest.mark.asyncio
async def test_complete_task_still_works_as_a_one_way_wrapper(tmp_path):
    provider = _provider(tmp_path)
    created = await provider.create_task("Task")

    completed = await provider.complete_task(created.id)

    assert completed.done is True
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_create_task_stores_due_date(tmp_path):
    provider = _provider(tmp_path)

    created = await provider.create_task("Task", due="2026-03-05")

    assert created.due == "2026-03-05"
    fetched = await provider.get_task(created.id)
    assert fetched.due == "2026-03-05"
