"""TaskProvider — abstraction over where tasks/todos actually live.

Mirrors tools/calendar/calendar_provider.py's CalendarProvider pattern:
LocalJSONTaskProvider is the local/default implementation,
GoogleTaskProvider (tools/tasks/google_task_provider.py) the real one --
AuraAgent's tasks now live in the user's actual Google Tasks account, in a
dedicated list it finds-or-creates itself (see google_task_provider.py's
ensure_aura_task_list). Keeping both behind this one interface means
task_tool.py's HITL-gated tool-calling contract and (if a backend is ever
added that a GUI page cares about) any REST layer never need to know which
concrete backend is active.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

#: Sentinel for update_task's nullable kwargs -- distinguishes "field not
#: passed, leave it alone" from "explicitly passed None, clear it" (same
#: idiom as tools/scheduler/schedule_store.py's own UNSET; each module
#: keeps its own instance, this one is TaskProvider's).
UNSET = object()


@dataclass
class Task:
    id: str
    title: str
    notes: str | None
    done: bool
    created_at: str  # ISO-8601
    completed_at: str | None
    due: str | None = None  # ISO-8601 date, e.g. "2026-03-05"


class TaskNotFoundError(Exception):
    """Raised when a task id doesn't exist in the provider's store."""


class TaskProvider(ABC):
    @abstractmethod
    async def list_tasks(self, include_completed: bool = True) -> list[Task]: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Task: ...

    @abstractmethod
    async def create_task(self, title: str, notes: str | None = None, due: str | None = None) -> Task: ...

    @abstractmethod
    async def update_task(
        self, task_id: str, *, title: str | None = None, notes: Any = UNSET, due: Any = UNSET
    ) -> Task:
        """Only touches fields actually passed -- title=None means "leave
        the title alone" (titles are never intentionally cleared to
        empty), but notes/due use the UNSET sentinel since both ARE
        legitimately nullable and a caller must be able to explicitly
        clear one (pass None) without that being indistinguishable from
        "didn't mention it" (the default, UNSET)."""
        ...

    @abstractmethod
    async def complete_task(self, task_id: str) -> Task: ...

    @abstractmethod
    async def set_task_done(self, task_id: str, done: bool) -> Task:
        """Generalizes complete_task into a real toggle -- checking AND
        unchecking. complete_task is kept as a thin `set_task_done(id,
        True)` wrapper on every implementation, for zero behavior change
        to its existing external contract."""
        ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> None: ...
