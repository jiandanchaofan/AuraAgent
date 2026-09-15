"""CalendarTool — the ToolSpec-facing wrapper around a CalendarProvider,
responsible for the Human-in-the-loop gating on destructive/high-risk
operations (delete_calendar_event, or update_calendar_event when the
target event is flagged `important`).

TODO (next iteration): implement register_calendar_tools() analogous to
tools/notes/notes_tool.py's register_notes_tools() — 4-5 ToolSpecs
(list/create/update/delete_calendar_event) wired to a CalendarProvider,
with delete/important-update handlers calling
`confirmation_channel.confirm(...)` before touching the provider and
returning a plain "user declined" Observation (not raising) on refusal.
"""
from __future__ import annotations

from confirmation.base import ConfirmationChannel
from tools.calendar.calendar_provider import CalendarProvider
from tools.registry import ToolRegistry


def register_calendar_tools(
    registry: ToolRegistry,
    provider: CalendarProvider,
    confirmation_channel: ConfirmationChannel,
) -> None:
    raise NotImplementedError("Calendar tools land in the next iteration — see module docstring.")
