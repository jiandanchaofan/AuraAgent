"""ScheduleStore -- persistence for user-defined scheduled tasks (N8
proactivity): a task description plus WHEN to run it, either once at a
specific time (`trigger_type="once"`, a plain ISO `run_at`) or repeatedly
(`trigger_type="recurring"`, a standard 5-field cron expression). See
tools/scheduler/scheduler_loop.py for the execution side -- this module is
purely storage + next-run-time bookkeeping, same JSON-file +
asyncio.Lock persistence pattern as MemoryStore/ProjectStore.

Not tied to a Project: `ScheduledTask.project_slug` is nullable -- a task
can be created from a Project's own chat (defaults to that Project) or
from an unrelated chat (defaults to unaffiliated, or an explicitly named
Project). See scheduler_tool.py's propose_scheduled_task for how a target
is resolved at creation time; project_slug only matters again at
*execution* time, when scheduler_loop.py decides what context to run in.

Next-run-time math for `recurring` schedules is delegated to `croniter`
rather than hand-rolled -- month-end/leap-year edge cases are a real,
well-known correctness pitfall in cron math, not worth reinventing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from croniter import croniter

#: Sentinel distinguishing "project_slug not passed, don't touch it" from
#: "project_slug=None, explicitly clear it" in update_schedule() below --
#: None is itself a valid target value (an unaffiliated task), so it can't
#: double as the "unset" default. Public (not `_UNSET`) because callers in
#: cli/service.py and gui/routes.py need to pass it through explicitly too.
UNSET = object()

_MAX_RESULT_SUMMARY_CHARS = 500


@dataclass
class ScheduledTask:
    id: str
    task: str
    trigger_type: str  # "once" | "recurring"
    project_slug: str | None
    run_at: str | None  # ISO datetime -- set iff trigger_type == "once"
    cron_expression: str | None  # 5-field cron -- set iff trigger_type == "recurring"
    enabled: bool
    created_at: str
    last_run_at: str | None
    last_result_summary: str | None
    next_run_at: str | None  # ISO datetime; None once a "once" task has run


def compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Next occurrence of `cron_expression` strictly after `after`. Raises
    ValueError for a malformed expression (croniter's own exception types
    vary by failure mode -- normalized to ValueError so callers only need
    to catch one thing, same convention as the rest of this module)."""
    try:
        return croniter(cron_expression, after).get_next(datetime)
    except Exception as exc:  # noqa: BLE001 - croniter raises several different exception types for bad input
        raise ValueError(f"Invalid cron expression '{cron_expression}': {exc}") from exc


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO datetime '{value}'.") from exc


class ScheduleStore:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text("[]", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self._file_path.read_text(encoding="utf-8"))

    def _save(self, raw: list[dict[str, Any]]) -> None:
        self._file_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _to_task(raw: dict[str, Any]) -> ScheduledTask:
        return ScheduledTask(**raw)

    async def list_schedules(self, project_slug: str | None = None, unaffiliated_only: bool = False) -> list[ScheduledTask]:
        async with self._lock:
            tasks = [self._to_task(raw) for raw in self._load()]
        if unaffiliated_only:
            return [t for t in tasks if t.project_slug is None]
        if project_slug is not None:
            return [t for t in tasks if t.project_slug == project_slug]
        return tasks

    async def get_schedule(self, schedule_id: str) -> ScheduledTask | None:
        async with self._lock:
            for raw in self._load():
                if raw["id"] == schedule_id:
                    return self._to_task(raw)
            return None

    async def create_schedule(
        self,
        task: str,
        trigger_type: str,
        run_at: str | None = None,
        cron_expression: str | None = None,
        project_slug: str | None = None,
    ) -> ScheduledTask:
        if trigger_type not in ("once", "recurring"):
            raise ValueError(f"Invalid trigger_type '{trigger_type}' (expected 'once' or 'recurring').")
        now = datetime.now()
        if trigger_type == "once":
            if not run_at:
                raise ValueError("run_at is required for a one-time ('once') schedule.")
            next_run = _parse_iso(run_at)
            cron_expression = None
        else:
            if not cron_expression:
                raise ValueError("cron_expression is required for a recurring schedule.")
            next_run = compute_next_run(cron_expression, now)
            run_at = None
        info = ScheduledTask(
            id=uuid.uuid4().hex[:8],
            task=task,
            trigger_type=trigger_type,
            project_slug=project_slug,
            run_at=run_at,
            cron_expression=cron_expression,
            enabled=True,
            created_at=now.isoformat(),
            last_run_at=None,
            last_result_summary=None,
            next_run_at=next_run.isoformat(),
        )
        async with self._lock:
            raw_list = self._load()
            raw_list.append(asdict(info))
            self._save(raw_list)
        return info

    async def update_schedule(
        self,
        schedule_id: str,
        task: str | None = None,
        run_at: str | None = None,
        cron_expression: str | None = None,
        project_slug: Any = UNSET,
    ) -> ScheduledTask:
        """Only touches fields that were actually passed. Passing `run_at`
        switches the schedule to trigger_type="once" (clearing
        cron_expression); passing `cron_expression` switches it to
        "recurring" (clearing run_at) -- passing both is nonsensical and
        the later one below wins, so callers should only ever pass one.
        Either one re-activates the schedule (`enabled=True`) and
        recomputes `next_run_at` -- useful for re-arming a completed
        "once" task with a new time rather than having to delete + recreate it."""
        async with self._lock:
            raw_list = self._load()
            for raw in raw_list:
                if raw["id"] != schedule_id:
                    continue
                if task is not None:
                    raw["task"] = task
                if project_slug is not UNSET:
                    raw["project_slug"] = project_slug
                retrigger = False
                if run_at is not None:
                    _parse_iso(run_at)  # validate before mutating
                    raw["trigger_type"] = "once"
                    raw["run_at"] = run_at
                    raw["cron_expression"] = None
                    retrigger = True
                if cron_expression is not None:
                    compute_next_run(cron_expression, datetime.now())  # validate before mutating
                    raw["trigger_type"] = "recurring"
                    raw["cron_expression"] = cron_expression
                    raw["run_at"] = None
                    retrigger = True
                if retrigger:
                    raw["enabled"] = True
                    now = datetime.now()
                    if raw["trigger_type"] == "once":
                        raw["next_run_at"] = _parse_iso(raw["run_at"]).isoformat()
                    else:
                        raw["next_run_at"] = compute_next_run(raw["cron_expression"], now).isoformat()
                self._save(raw_list)
                return self._to_task(raw)
            raise ValueError(f"No such schedule '{schedule_id}'.")

    async def set_enabled(self, schedule_id: str, enabled: bool) -> ScheduledTask:
        async with self._lock:
            raw_list = self._load()
            for raw in raw_list:
                if raw["id"] == schedule_id:
                    raw["enabled"] = enabled
                    self._save(raw_list)
                    return self._to_task(raw)
            raise ValueError(f"No such schedule '{schedule_id}'.")

    async def delete_schedule(self, schedule_id: str) -> None:
        async with self._lock:
            raw_list = self._load()
            remaining = [raw for raw in raw_list if raw["id"] != schedule_id]
            if len(remaining) == len(raw_list):
                raise ValueError(f"No such schedule '{schedule_id}'.")
            self._save(remaining)

    async def record_run(self, schedule_id: str, result_summary: str, ran_at: datetime | None = None) -> ScheduledTask:
        """Called once by scheduler_loop.py after each execution attempt
        (success or failure -- `result_summary` says which). A "once"
        schedule is disabled (not deleted) so it stays visible in the
        management UI with its result; a "recurring" one gets its
        next_run_at recomputed from `ran_at` (not from the missed
        original time), so an offline gap doesn't cause a burst of
        catch-up runs -- see scheduler_loop.py's module docstring."""
        ran_at = ran_at or datetime.now()
        async with self._lock:
            raw_list = self._load()
            for raw in raw_list:
                if raw["id"] == schedule_id:
                    raw["last_run_at"] = ran_at.isoformat()
                    raw["last_result_summary"] = result_summary[:_MAX_RESULT_SUMMARY_CHARS]
                    if raw["trigger_type"] == "once":
                        raw["enabled"] = False
                        raw["next_run_at"] = None
                    else:
                        raw["next_run_at"] = compute_next_run(raw["cron_expression"], ran_at).isoformat()
                    self._save(raw_list)
                    return self._to_task(raw)
            raise ValueError(f"No such schedule '{schedule_id}'.")

    async def list_due(self, now: datetime | None = None) -> list[ScheduledTask]:
        now = now or datetime.now()
        async with self._lock:
            tasks = [self._to_task(raw) for raw in self._load()]
        return [t for t in tasks if t.enabled and t.next_run_at is not None and _parse_iso(t.next_run_at) <= now]
