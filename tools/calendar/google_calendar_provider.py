"""Future real implementation slot for Google Calendar (OAuth).

Same CalendarProvider contract as LocalJSONCalendarProvider — implementing
this later requires zero changes to CalendarTool or the tool-calling
contract exposed to the Agent.
"""
from __future__ import annotations

from tools.calendar.calendar_provider import CalendarProvider


class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "GoogleCalendarProvider is not implemented yet — v1 uses LocalJSONCalendarProvider. "
            "See tools/calendar/calendar_provider.py for the contract to implement."
        )
