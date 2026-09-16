"""Unit tests for LocalJSONCalendarProvider's CRUD + on-disk persistence,
independent of the CalendarTool/HITL layer (see test_calendar_tool.py).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from tools.calendar.calendar_provider import CalendarEvent
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider


def _event(event_id="e1", title="Standup", day="2026-09-20", important=False) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title=title,
        start=datetime.fromisoformat(f"{day}T09:00:00"),
        end=datetime.fromisoformat(f"{day}T09:15:00"),
        important=important,
    )


@pytest.mark.asyncio
async def test_create_get_update_delete_round_trip(tmp_path):
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")

    await provider.create_event(_event())
    fetched = await provider.get_event("e1")
    assert fetched is not None and fetched.title == "Standup"

    updated = await provider.update_event("e1", {"title": "Standup (renamed)"})
    assert updated.title == "Standup (renamed)"

    await provider.delete_event("e1")
    assert await provider.get_event("e1") is None


@pytest.mark.asyncio
async def test_data_persists_across_provider_instances(tmp_path):
    events_file = tmp_path / "events.json"
    await LocalJSONCalendarProvider(events_file).create_event(_event(event_id="e1", title="Persisted"))

    reloaded = LocalJSONCalendarProvider(events_file)
    fetched = await reloaded.get_event("e1")
    assert fetched is not None and fetched.title == "Persisted"


@pytest.mark.asyncio
async def test_list_events_filters_by_date_range_and_sorts(tmp_path):
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")
    await provider.create_event(_event(event_id="e2", title="Later", day="2026-09-25"))
    await provider.create_event(_event(event_id="e1", title="Earlier", day="2026-09-05"))
    await provider.create_event(_event(event_id="e3", title="OutOfRange", day="2026-10-05"))

    events = await provider.list_events(date(2026, 9, 1), date(2026, 9, 30))

    assert [e.title for e in events] == ["Earlier", "Later"]


@pytest.mark.asyncio
async def test_create_duplicate_id_raises(tmp_path):
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")
    await provider.create_event(_event(event_id="dup"))
    with pytest.raises(ValueError):
        await provider.create_event(_event(event_id="dup"))


@pytest.mark.asyncio
async def test_update_missing_event_raises_keyerror(tmp_path):
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")
    with pytest.raises(KeyError):
        await provider.update_event("missing", {"title": "x"})


@pytest.mark.asyncio
async def test_delete_missing_event_raises_keyerror(tmp_path):
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")
    with pytest.raises(KeyError):
        await provider.delete_event("missing")


@pytest.mark.asyncio
async def test_concurrent_creates_do_not_lose_updates(tmp_path):
    """Regression test for Multi-Agent concurrent tool dispatch (see
    core/react_engine.py's asyncio.gather-based dispatch): without the
    per-instance asyncio.Lock, concurrent create_event() calls could each
    read the same pre-mutation snapshot and overwrite each other on save."""
    provider = LocalJSONCalendarProvider(tmp_path / "events.json")

    await asyncio.gather(
        *(provider.create_event(_event(event_id=f"e{i}", title=f"Event {i}")) for i in range(20))
    )

    events = await provider.list_events(date(2026, 9, 20), date(2026, 9, 20))
    assert len(events) == 20
    assert {e.event_id for e in events} == {f"e{i}" for i in range(20)}
