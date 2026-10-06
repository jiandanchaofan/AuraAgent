"""SwappableTaskProvider — a TaskProvider that forwards every call to
whichever concrete provider it currently holds, and can be swapped at
runtime. Mirrors tools/calendar/swappable_calendar_provider.py's exact
pattern, applied one level down for tasks.

register_task_tools (tools/tasks/task_tool.py) is handed this object as
its `provider` argument instead of a concrete LocalJSONTaskProvider/
GoogleTaskProvider directly. That single level of indirection is what
lets the /tasks connect CLI command (cli/commands.py) switch the whole
app over to Google Tasks at once, immediately, by calling set_current()
here -- without this, task_tool.py would hold a frozen reference to
whatever provider core/bootstrap.py originally constructed, and switching
would need a restart.
"""
from __future__ import annotations

from typing import Any

from tools.tasks.task_provider import UNSET, Task, TaskProvider


class SwappableTaskProvider(TaskProvider):
    def __init__(self, initial: TaskProvider, initial_name: str) -> None:
        self._current = initial
        #: "local" | "google" -- not part of the TaskProvider interface
        #: itself, tracked here purely so /tasks can report which one is
        #: active without needing to inspect the concrete provider's type.
        self.backend_name = initial_name

    def set_current(self, new_provider: TaskProvider, name: str) -> None:
        self._current = new_provider
        self.backend_name = name

    async def list_tasks(self, include_completed: bool = True) -> list[Task]:
        return await self._current.list_tasks(include_completed=include_completed)

    async def get_task(self, task_id: str) -> Task:
        return await self._current.get_task(task_id)

    async def create_task(self, title: str, notes: str | None = None, due: str | None = None) -> Task:
        return await self._current.create_task(title, notes, due)

    async def update_task(self, task_id: str, *, title: str | None = None, notes: Any = UNSET, due: Any = UNSET) -> Task:
        return await self._current.update_task(task_id, title=title, notes=notes, due=due)

    async def complete_task(self, task_id: str) -> Task:
        return await self._current.complete_task(task_id)

    async def set_task_done(self, task_id: str, done: bool) -> Task:
        return await self._current.set_task_done(task_id, done)

    async def delete_task(self, task_id: str) -> None:
        await self._current.delete_task(task_id)
