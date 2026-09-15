"""CalendarProvider — abstraction over where calendar events actually live.

Mirrors the LLMProvider pattern one level down: LocalJSONCalendarProvider
is the v1 implementation, GoogleCalendarProvider is a future OAuth-backed
implementation of the exact same contract, so swapping providers requires
zero changes to CalendarTool or the tool-calling contract exposed to the
Agent.

TODO (next iteration): flesh out CalendarTool's HITL gating and
LocalJSONCalendarProvider's full CRUD logic. This file defines the
contract now so that work can proceed without redesigning it later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    important: bool = False
    description: str = ""


class CalendarProvider(ABC):
    @abstractmethod
    async def list_events(self, start: date, end: date) -> list[CalendarEvent]: ...

    @abstractmethod
    async def get_event(self, event_id: str) -> CalendarEvent | None: ...

    @abstractmethod
    async def create_event(self, event: CalendarEvent) -> CalendarEvent: ...

    @abstractmethod
    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent: ...

    @abstractmethod
    async def delete_event(self, event_id: str) -> None: ...
