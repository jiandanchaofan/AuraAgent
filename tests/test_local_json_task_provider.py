"""Unit tests for LocalJSONTaskProvider's CRUD + on-disk persistence,
independent of the task_tool/HITL layer (see test_task_tool.py).
Mirrors tests/test_local_json_calendar.py.
"""
from __future__ import annotations

import pytest

from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_provider import TaskNotFoundError


@pytest.mark.asyncio
async def test_create_get_complete_delete_round_trip(tmp_path):
    provider = LocalJSONTaskProvider(tmp_path / "tasks.json")

    created = await provider.create_task("Write report", notes="due Friday")
    fetched = await provider.get_task(created.id)
    assert fetched.title == "Write report"
    assert fetched.notes == "due Friday"
    assert fetched.done is False

    completed = await provider.complete_task(created.id)
    assert completed.done is True
    assert completed.completed_at is not None

    await provider.delete_task(created.id)
    with pytest.raises(TaskNotFoundError):
        await provider.get_task(created.id)


@pytest.mark.asyncio
async def test_data_persists_across_provider_instances(tmp_path):
    file_path = tmp_path / "tasks.json"
    created = await LocalJSONTaskProvider(file_path).create_task("Persisted task")

    reloaded = LocalJSONTaskProvider(file_path)
    fetched = await reloaded.get_task(created.id)
    assert fetched.title == "Persisted task"


@pytest.mark.asyncio
async def test_list_tasks_excludes_completed_when_requested(tmp_path):
    provider = LocalJSONTaskProvider(tmp_path / "tasks.json")
    open_task = await provider.create_task("Open task")
    done_task = await provider.create_task("Done task")
    await provider.complete_task(done_task.id)

    all_tasks = await provider.list_tasks(include_completed=True)
    open_only = await provider.list_tasks(include_completed=False)

    assert {t.id for t in all_tasks} == {open_task.id, done_task.id}
    assert {t.id for t in open_only} == {open_task.id}


@pytest.mark.asyncio
async def test_get_missing_task_raises(tmp_path):
    provider = LocalJSONTaskProvider(tmp_path / "tasks.json")
    with pytest.raises(TaskNotFoundError):
        await provider.get_task("missing")


@pytest.mark.asyncio
async def test_complete_missing_task_raises(tmp_path):
    provider = LocalJSONTaskProvider(tmp_path / "tasks.json")
    with pytest.raises(TaskNotFoundError):
        await provider.complete_task("missing")


@pytest.mark.asyncio
async def test_delete_missing_task_raises(tmp_path):
    provider = LocalJSONTaskProvider(tmp_path / "tasks.json")
    with pytest.raises(TaskNotFoundError):
        await provider.delete_task("missing")
