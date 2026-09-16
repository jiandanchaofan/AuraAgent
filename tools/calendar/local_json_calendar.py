"""Local JSON-backed CalendarProvider.

Stores events as a flat JSON array at `events_file` (default:
sandbox/calendar/events.json). Each event is serialized with ISO-8601
datetime strings so the file stays human-readable and diffable — useful
while learning/debugging the agent's calendar actions.

Concurrency: every public method's full body runs under one
asyncio.Lock per instance. Without it, this class's "safety" would be
accidental — plain load-mutate-save with no `await` in between happens to
be atomic under today's cooperative scheduling, but that stops being true
the moment two coroutines (e.g. two concurrently-running Multi-Agent
Workers, see core/react_engine.py's concurrent tool dispatch) both reach
this provider through the shared ToolRegistry at once. The lock spans
each ENTIRE method (reads included, not just writes) because the critical
section is the whole load-through-save transaction — locking only
`_save()` would still let two concurrent `create_event()` calls both read
the same pre-mutation snapshot and silently lose one update. One lock per
instance is sufficient because main.py constructs exactly one provider
instance per file, registered once into the single shared ToolRegistry —
nothing should ever construct a second instance pointed at the same file.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tools.calendar.calendar_provider import CalendarEvent, CalendarProvider


class LocalJSONCalendarProvider(CalendarProvider):
    def __init__(self, events_file: Path) -> None:
        self._events_file = events_file
        self._events_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._events_file.exists():
            self._events_file.write_text("[]", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self._events_file.read_text(encoding="utf-8"))

    def _save(self, raw_events: list[dict[str, Any]]) -> None:
        self._events_file.write_text(
            json.dumps(raw_events, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _to_event(raw: dict[str, Any]) -> CalendarEvent:
        return CalendarEvent(
            event_id=raw["event_id"],
            title=raw["title"],
            start=datetime.fromisoformat(raw["start"]),
            end=datetime.fromisoformat(raw["end"]),
            important=raw.get("important", False),
            description=raw.get("description", ""),
        )

    @staticmethod
    def _to_raw(event: CalendarEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "title": event.title,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "important": event.important,
            "description": event.description,
        }

    async def list_events(self, start: date, end: date) -> list[CalendarEvent]:
        async with self._lock:
            events = [self._to_event(raw) for raw in self._load()]
            in_range = [e for e in events if start <= e.start.date() <= end]
            return sorted(in_range, key=lambda e: e.start)

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        async with self._lock:
            for raw in self._load():
                if raw["event_id"] == event_id:
                    return self._to_event(raw)
            return None

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        async with self._lock:
            raw_events = self._load()
            if any(raw["event_id"] == event.event_id for raw in raw_events):
                raise ValueError(f"Event id '{event.event_id}' already exists")
            raw_events.append(self._to_raw(event))
            self._save(raw_events)
            return event

    async def update_event(self, event_id: str, patch: dict[str, Any]) -> CalendarEvent:
        """`patch` values must already be JSON-serializable (e.g. ISO strings
        for `start`/`end`, not datetime objects) — callers build it that way
        so this provider never has to guess a caller's representation."""
        async with self._lock:
            raw_events = self._load()
            for raw in raw_events:
                if raw["event_id"] == event_id:
                    raw.update(patch)
                    self._save(raw_events)
                    return self._to_event(raw)
            raise KeyError(f"Event id '{event_id}' not found")

    async def delete_event(self, event_id: str) -> None:
        async with self._lock:
            raw_events = self._load()
            remaining = [raw for raw in raw_events if raw["event_id"] != event_id]
            if len(remaining) == len(raw_events):
                raise KeyError(f"Event id '{event_id}' not found")
            self._save(remaining)
