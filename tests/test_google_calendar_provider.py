"""Tests for tools/calendar/google_calendar_provider.py. Uses
httpx.MockTransport (no extra mocking dependency) to fake Calendar API v3
HTTP responses -- no real Google account or network access is used or
needed. Credentials are a lightweight fake (not a real google-auth
Credentials object) since the provider only ever touches
.expired/.token/.refresh()/.to_json() on it.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import httpx
import pytest

from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.calendar.calendar_provider import CalendarEvent
from tools.calendar.google_calendar_provider import GoogleCalendarProvider

_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class FakeCredentials:
    def __init__(self, token: str = "valid-token", expired: bool = False) -> None:
        self.token = token
        self.expired = expired
        self.refresh_calls = 0

    def refresh(self, request) -> None:
        self.refresh_calls += 1
        self.token = "refreshed-token"
        self.expired = False

    def to_json(self) -> str:
        return json.dumps({"token": self.token})


def _provider(tmp_path, handler, credentials=None):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    credentials = credentials or FakeCredentials()
    token_file = tmp_path / "google_token.json"
    return GoogleCalendarProvider(credentials, token_file, http_client), credentials, token_file


@pytest.mark.asyncio
async def test_create_event_uses_summary_field_not_title(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        captured["body"] = body
        response_body = dict(body)
        return httpx.Response(200, json=response_body)

    provider, _, _ = _provider(tmp_path, handler)
    event = CalendarEvent(
        event_id="abc12345", title="Team sync", start=datetime(2026, 9, 20, 14, 0), end=datetime(2026, 9, 20, 15, 0)
    )

    created = await provider.create_event(event)

    assert captured["body"]["summary"] == "Team sync"
    assert "title" not in captured["body"]
    assert captured["body"]["id"] == "abc12345"
    assert created.title == "Team sync"


@pytest.mark.asyncio
async def test_create_event_conflict_raises_value_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "already exists"})

    provider, _, _ = _provider(tmp_path, handler)
    event = CalendarEvent(event_id="dup", title="x", start=datetime(2026, 1, 1, 9), end=datetime(2026, 1, 1, 10))

    with pytest.raises(ValueError):
        await provider.create_event(event)


@pytest.mark.asyncio
async def test_important_round_trips_through_extended_properties(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["extendedProperties"]["private"]["aura_important"] == "true"
        return httpx.Response(200, json=body)

    provider, _, _ = _provider(tmp_path, handler)
    event = CalendarEvent(
        event_id="e1", title="Board meeting", start=datetime(2026, 1, 1, 9), end=datetime(2026, 1, 1, 10),
        important=True,
    )

    created = await provider.create_event(event)

    assert created.important is True


@pytest.mark.asyncio
async def test_naive_datetime_round_trips_through_rfc3339(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Sent value must be a valid, timezone-aware RFC3339 string.
        sent = datetime.fromisoformat(body["start"]["dateTime"])
        assert sent.tzinfo is not None
        return httpx.Response(200, json=body)

    provider, _, _ = _provider(tmp_path, handler)
    original_start = datetime(2026, 9, 20, 14, 30, 0)
    event = CalendarEvent(event_id="e2", title="x", start=original_start, end=datetime(2026, 9, 20, 15, 0))

    created = await provider.create_event(event)

    assert created.start == original_start
    assert created.start.tzinfo is None


@pytest.mark.asyncio
async def test_update_event_sends_patch_with_only_changed_fields(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        body = json.loads(request.content) if request.content else {}
        captured["body"] = body
        full = {
            "id": "e3",
            "summary": body.get("summary", "Old title"),
            "description": "Old description",
            "start": {"dateTime": "2026-01-01T09:00:00-08:00"},
            "end": {"dateTime": "2026-01-01T10:00:00-08:00"},
        }
        return httpx.Response(200, json=full)

    provider, _, _ = _provider(tmp_path, handler)

    await provider.update_event("e3", {"title": "New title"})

    assert captured["method"] == "PATCH"
    assert captured["body"] == {"summary": "New title"}
    assert "description" not in captured["body"]
    assert "start" not in captured["body"]


@pytest.mark.asyncio
async def test_get_event_404_returns_none(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider, _, _ = _provider(tmp_path, handler)

    assert await provider.get_event("missing") is None


@pytest.mark.asyncio
async def test_update_event_404_raises_key_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider, _, _ = _provider(tmp_path, handler)

    with pytest.raises(KeyError):
        await provider.update_event("missing", {"title": "x"})


@pytest.mark.asyncio
async def test_delete_event_404_raises_key_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider, _, _ = _provider(tmp_path, handler)

    with pytest.raises(KeyError):
        await provider.delete_event("missing")


@pytest.mark.asyncio
async def test_delete_event_success(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    provider, _, _ = _provider(tmp_path, handler)

    await provider.delete_event("e4")  # must not raise


@pytest.mark.asyncio
async def test_token_refresh_happens_once_for_concurrent_calls(tmp_path):
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    credentials = FakeCredentials(expired=True)
    provider, credentials, token_file = _provider(tmp_path, handler, credentials=credentials)

    await asyncio.gather(
        provider.list_events(date(2026, 1, 1), date(2026, 1, 31)),
        provider.list_events(date(2026, 1, 1), date(2026, 1, 31)),
    )

    assert credentials.refresh_calls == 1
    assert token_file.exists()


@pytest.mark.asyncio
async def test_list_events_date_filter_matches_local_provider_semantics(tmp_path):
    # A multi-day event that STARTS before the window but overlaps it --
    # Google's timeMin/timeMax would include it (overlap semantics), but
    # LocalJSONCalendarProvider's "event's own start date in [start,end]"
    # semantics excludes it, and GoogleCalendarProvider must match that.
    raw_items = [
        {
            "id": "in-range",
            "summary": "In range",
            "start": {"dateTime": "2026-03-10T09:00:00-08:00"},
            "end": {"dateTime": "2026-03-10T10:00:00-08:00"},
        },
        {
            "id": "starts-before-overlaps-into-range",
            "summary": "Overlaps but starts earlier",
            "start": {"dateTime": "2026-03-01T09:00:00-08:00"},
            "end": {"dateTime": "2026-03-12T10:00:00-08:00"},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": raw_items})

    provider, _, _ = _provider(tmp_path, handler)

    events = await provider.list_events(date(2026, 3, 5), date(2026, 3, 15))

    assert [e.event_id for e in events] == ["in-range"]

    # Cross-check against LocalJSONCalendarProvider given the equivalent
    # data, to confirm the two backends agree on the same semantics.
    local = LocalJSONCalendarProvider(tmp_path / "local_events.json")
    await local.create_event(
        CalendarEvent(event_id="in-range", title="In range", start=datetime(2026, 3, 10, 9), end=datetime(2026, 3, 10, 10))
    )
    await local.create_event(
        CalendarEvent(
            event_id="starts-before-overlaps-into-range", title="x",
            start=datetime(2026, 3, 1, 9), end=datetime(2026, 3, 12, 10),
        )
    )
    local_events = await local.list_events(date(2026, 3, 5), date(2026, 3, 15))
    assert [e.event_id for e in events] == [e.event_id for e in local_events]


@pytest.mark.asyncio
async def test_list_events_handles_all_day_events_without_crashing(tmp_path):
    raw_items = [
        {"id": "all-day", "summary": "Holiday", "start": {"date": "2026-03-10"}, "end": {"date": "2026-03-11"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": raw_items})

    provider, _, _ = _provider(tmp_path, handler)

    events = await provider.list_events(date(2026, 3, 5), date(2026, 3, 15))

    assert len(events) == 1
    assert events[0].start == datetime(2026, 3, 10, 0, 0)
