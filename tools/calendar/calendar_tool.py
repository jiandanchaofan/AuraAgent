"""CalendarTool — the ToolSpec-facing wrapper around a CalendarProvider,
responsible for the Human-in-the-loop gating on destructive/high-risk
operations:

  - delete_calendar_event always asks for confirmation.
  - update_calendar_event asks for confirmation only when the event being
    modified is currently flagged `important` — routine events can be
    edited freely, but anything the user marked important gets a pause.

A decline is never an exception: it comes back as a plain-text Observation
("User declined...") so the LLM reasons about it like any other tool
result, exactly matching how core/react_engine.py treats every Observation.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.calendar.calendar_provider import CalendarEvent, CalendarProvider
from tools.registry import ToolRegistry


def register_calendar_tools(
    registry: ToolRegistry,
    provider: CalendarProvider,
    confirmation_channel: ConfirmationChannel,
) -> None:
    async def list_calendar_events(args: dict[str, Any]) -> str:
        start = date.fromisoformat(args["start_date"])
        end = date.fromisoformat(args["end_date"])
        events = await provider.list_events(start, end)
        if not events:
            return f"No events between {start.isoformat()} and {end.isoformat()}."
        lines = [
            f"- [{e.event_id}] {e.title} ({e.start.isoformat()} -> {e.end.isoformat()})"
            + (" [IMPORTANT]" if e.important else "")
            for e in events
        ]
        return f"Found {len(events)} event(s):\n" + "\n".join(lines)

    async def create_calendar_event(args: dict[str, Any]) -> str:
        event = CalendarEvent(
            event_id=uuid.uuid4().hex[:8],
            title=args["title"],
            start=datetime.fromisoformat(args["start"]),
            end=datetime.fromisoformat(args["end"]),
            important=args.get("important", False),
            description=args.get("description", ""),
        )
        created = await provider.create_event(event)
        return f"Created event '{created.title}' (id={created.event_id})."

    async def update_calendar_event(args: dict[str, Any]) -> str:
        event_id = args["event_id"]
        existing = await provider.get_event(event_id)
        if existing is None:
            raise ToolExecutionError(f"Event not found: '{event_id}'")

        if existing.important:
            approved = await confirmation_channel.confirm(
                ConfirmationRequest(
                    tool_name="update_calendar_event",
                    arguments=args,
                    reason=(
                        f"Modify IMPORTANT event '{existing.title}' "
                        f"({existing.start.isoformat()})?"
                    ),
                    risk_level="important_modification",
                )
            )
            if not approved:
                return "User declined the operation. Event was not modified."

        patch: dict[str, Any] = {}
        for key in ("title", "important", "description"):
            if key in args:
                patch[key] = args[key]
        if "start" in args:
            patch["start"] = datetime.fromisoformat(args["start"]).isoformat()
        if "end" in args:
            patch["end"] = datetime.fromisoformat(args["end"]).isoformat()

        updated = await provider.update_event(event_id, patch)
        return f"Updated event '{updated.title}' (id={updated.event_id})."

    async def delete_calendar_event(args: dict[str, Any]) -> str:
        event_id = args["event_id"]
        existing = await provider.get_event(event_id)
        if existing is None:
            raise ToolExecutionError(f"Event not found: '{event_id}'")

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="delete_calendar_event",
                arguments=args,
                reason=f"Delete event '{existing.title}' ({existing.start.isoformat()})?",
                risk_level="destructive",
            )
        )
        if not approved:
            return "User declined the operation. Event was not deleted."

        await provider.delete_event(event_id)
        return f"Deleted event '{existing.title}' (id={event_id})."

    registry.register(
        ToolSpec(
            name="list_calendar_events",
            description="List calendar events between two dates (inclusive).",
            input_schema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO date, e.g. '2026-09-01'"},
                    "end_date": {"type": "string", "description": "ISO date, e.g. '2026-09-30'"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        list_calendar_events,
    )
    registry.register(
        ToolSpec(
            name="create_calendar_event",
            description="Create a new calendar event.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "ISO datetime, e.g. '2026-09-20T14:00:00'"},
                    "end": {"type": "string", "description": "ISO datetime, e.g. '2026-09-20T15:00:00'"},
                    "important": {"type": "boolean", "description": "Flag as important; defaults to false"},
                    "description": {"type": "string"},
                },
                "required": ["title", "start", "end"],
            },
        ),
        create_calendar_event,
    )
    registry.register(
        ToolSpec(
            name="update_calendar_event",
            description=(
                "Modify an existing calendar event's fields. If the event is flagged "
                "important, the user will be asked to confirm before the change is applied."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "ISO datetime"},
                    "end": {"type": "string", "description": "ISO datetime"},
                    "important": {"type": "boolean"},
                    "description": {"type": "string"},
                },
                "required": ["event_id"],
            },
        ),
        update_calendar_event,
    )
    registry.register(
        ToolSpec(
            name="delete_calendar_event",
            description="Delete a calendar event by id. Always asks the user to confirm before deleting.",
            input_schema={
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        ),
        delete_calendar_event,
    )
