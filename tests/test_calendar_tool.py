"""Tests for the local JSON calendar tools, focused on the
Human-in-the-loop confirmation gating: delete always asks, update only
asks when the target event is flagged `important`, and a decline is a
plain Observation rather than an exception.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.calendar.calendar_tool import register_calendar_tools
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.registry import ToolRegistry


@pytest.fixture
def registry_and_provider(tmp_path):
    events_file = tmp_path / "calendar" / "events.json"
    provider = LocalJSONCalendarProvider(events_file)
    return provider


async def _create_event(registry, title="Team Sync", important=False):
    result = await registry.dispatch(
        "create_calendar_event",
        {
            "title": title,
            "start": "2026-09-20T14:00:00",
            "end": "2026-09-20T15:00:00",
            "important": important,
        },
    )
    # "Created event 'Team Sync' (id=abcd1234)."
    event_id = result.split("id=")[1].rstrip(").")
    return event_id


@pytest.mark.asyncio
async def test_create_and_list_round_trip(registry_and_provider):
    provider = registry_and_provider
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, FakeConfirmationChannel(decision=True))

    await _create_event(registry, title="Team Sync")
    result = await registry.dispatch(
        "list_calendar_events", {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    )
    assert "Team Sync" in result


@pytest.mark.asyncio
async def test_delete_always_asks_and_proceeds_on_approval(registry_and_provider):
    provider = registry_and_provider
    confirmation = FakeConfirmationChannel(decision=True)
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, confirmation)

    event_id = await _create_event(registry, title="Dentist")
    result = await registry.dispatch("delete_calendar_event", {"event_id": event_id})

    assert "Deleted" in result
    assert len(confirmation.requests) == 1
    assert await provider.get_event(event_id) is None


@pytest.mark.asyncio
async def test_delete_declined_leaves_event_untouched(registry_and_provider):
    provider = registry_and_provider
    confirmation = FakeConfirmationChannel(decision=False)
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, confirmation)

    event_id = await _create_event(registry, title="Dentist")
    result = await registry.dispatch("delete_calendar_event", {"event_id": event_id})

    assert "declined" in result.lower()
    assert await provider.get_event(event_id) is not None


@pytest.mark.asyncio
async def test_update_routine_event_skips_confirmation(registry_and_provider):
    provider = registry_and_provider
    confirmation = FakeConfirmationChannel(decision=True)
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, confirmation)

    event_id = await _create_event(registry, title="Standup", important=False)
    result = await registry.dispatch(
        "update_calendar_event", {"event_id": event_id, "title": "Standup (moved)"}
    )

    assert "Updated" in result
    assert confirmation.requests == []  # never asked


@pytest.mark.asyncio
async def test_update_important_event_asks_and_applies_on_approval(registry_and_provider):
    provider = registry_and_provider
    confirmation = FakeConfirmationChannel(decision=True)
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, confirmation)

    event_id = await _create_event(registry, title="Board Meeting", important=True)
    result = await registry.dispatch(
        "update_calendar_event", {"event_id": event_id, "title": "Board Meeting (rescheduled)"}
    )

    assert "Updated" in result
    assert len(confirmation.requests) == 1
    updated = await provider.get_event(event_id)
    assert updated.title == "Board Meeting (rescheduled)"


@pytest.mark.asyncio
async def test_update_important_event_declined_leaves_it_unchanged(registry_and_provider):
    provider = registry_and_provider
    confirmation = FakeConfirmationChannel(decision=False)
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, confirmation)

    event_id = await _create_event(registry, title="Board Meeting", important=True)
    result = await registry.dispatch(
        "update_calendar_event", {"event_id": event_id, "title": "Board Meeting (rescheduled)"}
    )

    assert "declined" in result.lower()
    unchanged = await provider.get_event(event_id)
    assert unchanged.title == "Board Meeting"


@pytest.mark.asyncio
async def test_delete_missing_event_raises_tool_execution_error(registry_and_provider):
    provider = registry_and_provider
    registry = ToolRegistry()
    register_calendar_tools(registry, provider, FakeConfirmationChannel(decision=True))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("delete_calendar_event", {"event_id": "nonexistent"})
