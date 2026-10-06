"""Local JSON-backed TaskProvider.

Mirrors tools/calendar/local_json_calendar.py's LocalJSONCalendarProvider:
a flat JSON array file, human-readable ISO-8601 timestamps, short
uuid4-derived ids, and — for the same reasons documented at length in that
module — one asyncio.Lock per instance guarding each ENTIRE public
method's body against concurrent Multi-Agent access.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.tasks.task_provider import UNSET, Task, TaskNotFoundError, TaskProvider


class LocalJSONTaskProvider(TaskProvider):
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text("[]", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self._file_path.read_text(encoding="utf-8"))

    def _save(self, raw_tasks: list[dict[str, Any]]) -> None:
        self._file_path.write_text(
            json.dumps(raw_tasks, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _to_task(raw: dict[str, Any]) -> Task:
        return Task(
            id=raw["id"],
            title=raw["title"],
            notes=raw.get("notes"),
            done=raw.get("done", False),
            created_at=raw["created_at"],
            completed_at=raw.get("completed_at"),
            due=raw.get("due"),
        )

    async def list_tasks(self, include_completed: bool = True) -> list[Task]:
        async with self._lock:
            tasks = [self._to_task(raw) for raw in self._load()]
            if not include_completed:
                tasks = [t for t in tasks if not t.done]
            return sorted(tasks, key=lambda t: t.created_at)

    async def get_task(self, task_id: str) -> Task:
        async with self._lock:
            for raw in self._load():
                if raw["id"] == task_id:
                    return self._to_task(raw)
            raise TaskNotFoundError(f"Task id '{task_id}' not found")

    async def create_task(self, title: str, notes: str | None = None, due: str | None = None) -> Task:
        async with self._lock:
            raw_tasks = self._load()
            raw = {
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "notes": notes,
                "done": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
                "due": due,
            }
            raw_tasks.append(raw)
            self._save(raw_tasks)
            return self._to_task(raw)

    async def update_task(
        self, task_id: str, *, title: str | None = None, notes: Any = UNSET, due: Any = UNSET
    ) -> Task:
        async with self._lock:
            raw_tasks = self._load()
            for raw in raw_tasks:
                if raw["id"] == task_id:
                    if title is not None:
                        raw["title"] = title
                    if notes is not UNSET:
                        raw["notes"] = notes
                    if due is not UNSET:
                        raw["due"] = due
                    self._save(raw_tasks)
                    return self._to_task(raw)
            raise TaskNotFoundError(f"Task id '{task_id}' not found")

    async def complete_task(self, task_id: str) -> Task:
        return await self.set_task_done(task_id, True)

    async def set_task_done(self, task_id: str, done: bool) -> Task:
        async with self._lock:
            raw_tasks = self._load()
            for raw in raw_tasks:
                if raw["id"] == task_id:
                    raw["done"] = done
                    raw["completed_at"] = datetime.now(timezone.utc).isoformat() if done else None
                    self._save(raw_tasks)
                    return self._to_task(raw)
            raise TaskNotFoundError(f"Task id '{task_id}' not found")

    async def delete_task(self, task_id: str) -> None:
        async with self._lock:
            raw_tasks = self._load()
            remaining = [raw for raw in raw_tasks if raw["id"] != task_id]
            if len(remaining) == len(raw_tasks):
                raise TaskNotFoundError(f"Task id '{task_id}' not found")
            self._save(remaining)
