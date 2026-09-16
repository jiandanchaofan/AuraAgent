"""TaskProvider — abstraction over where tasks/todos actually live.

Mirrors tools/calendar/calendar_provider.py's CalendarProvider pattern:
LocalJSONTaskProvider is the only implementation for now, but keeping the
abstraction means a future provider (e.g. a shared team task tracker)
could be swapped in without touching task_tool.py's HITL-gated
tool-calling contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Task:
    id: str
    title: str
    notes: str | None
    done: bool
    created_at: str  # ISO-8601
    completed_at: str | None


class TaskNotFoundError(Exception):
    """Raised when a task id doesn't exist in the provider's store."""


class TaskProvider(ABC):
    @abstractmethod
    async def list_tasks(self, include_completed: bool = True) -> list[Task]: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Task: ...

    @abstractmethod
    async def create_task(self, title: str, notes: str | None = None) -> Task: ...

    @abstractmethod
    async def complete_task(self, task_id: str) -> Task: ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> None: ...
