"""Local JSON-backed CalendarProvider.

TODO (next iteration): implement full CRUD against a JSON file at
`events_file` (settings.calendar_events_file). Stubbed for now so the
interface and directory layout are in place; the Human-in-the-loop wiring
in CalendarTool lands in the same pass as this implementation.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from tools.calendar.calendar_provider import CalendarEvent, CalendarProvider

_NOT_IMPLEMENTED = "LocalJSONCalendarProvider is scaffolded but not yet implemented — see module docstring."


class LocalJSONCalendarProvider(CalendarProvider):
    def __init__(self, events_file: Path) -> None:
        self._events_file = events_file

    async def list_events(self, start: date, end: date) -> list[CalendarEvent]:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def delete_event(self, event_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)
