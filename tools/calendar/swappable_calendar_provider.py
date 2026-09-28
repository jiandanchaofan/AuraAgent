"""SwappableCalendarProvider — a CalendarProvider that forwards every call
to whichever concrete provider it currently holds, and can be swapped at
runtime. Mirrors providers/swappable_provider.py::SwappableProvider's
exact pattern, applied one level down.

register_calendar_tools (tools/calendar/calendar_tool.py) is handed this
object as its `provider` argument instead of a concrete
LocalJSONCalendarProvider/GoogleCalendarProvider directly. That single
level of indirection is what lets the /calendar connect CLI command
(cli/commands.py) switch the whole app over to Google Calendar at once,
immediately, by calling set_current() here -- without this, calendar_tool.py
would hold a frozen reference to whatever provider core/bootstrap.py
originally constructed, and switching would need a restart.
"""
from __future__ import annotations

from datetime import date

from tools.calendar.calendar_provider import CalendarEvent, CalendarProvider


class SwappableCalendarProvider(CalendarProvider):
    def __init__(self, initial: CalendarProvider, initial_name: str) -> None:
        self._current = initial
        #: "local" | "google" -- not part of the CalendarProvider interface
        #: itself, tracked here purely so /calendar can report which one is
        #: active without needing to inspect the concrete provider's type.
        self.backend_name = initial_name

    def set_current(self, new_provider: CalendarProvider, name: str) -> None:
        self._current = new_provider
        self.backend_name = name

    async def list_events(self, start: date, end: date) -> list[CalendarEvent]:
        return await self._current.list_events(start, end)

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        return await self._current.get_event(event_id)

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        return await self._current.create_event(event)

    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent:
        return await self._current.update_event(event_id, patch)

    async def delete_event(self, event_id: str) -> None:
        await self._current.delete_event(event_id)
