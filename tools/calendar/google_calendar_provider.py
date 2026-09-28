"""Real Google Calendar implementation of CalendarProvider — talks to the
Calendar API v3 directly over the shared httpx.AsyncClient (no
google-api-python-client; see tools/calendar/google_auth.py's docstring
for why OAuth itself still goes through Google's own library).

Same CalendarProvider contract as LocalJSONCalendarProvider — CalendarTool
requires zero changes to use this instead. A few deliberate translation
points make that true despite the two backends being very different:

  - Google's event field is `summary`, not `title`.
  - `important` has no native Google Calendar concept; stored in
    extendedProperties.private.aura_important ("true"/"false") — a
    private, app-only field Google explicitly supports for exactly this
    kind of metadata that shouldn't show up in the visible event.
  - Every datetime in this whole system (LocalJSONCalendarProvider,
    CalendarTool's input schema examples) is naive and implicitly means
    "local wall-clock time" -- there is no timezone concept anywhere
    else. To stay a drop-in replacement, naive datetimes are converted
    to aware ones via Python's own documented behavior of
    `naive_dt.astimezone()` ("if self is naive, it is presumed to
    represent time in the system timezone") before being sent to Google,
    and folded back to naive local time on the way back
    (`.astimezone().replace(tzinfo=None)`).
  - update_event's `patch` (built by CalendarTool) is a PARTIAL dict of
    only the fields that actually changed -- this MUST be sent as an
    HTTP PATCH (events.patch), never PUT (events.update, which replaces
    the whole resource and would silently wipe any field not present in
    `patch`, e.g. description when only the title changed).

Concurrency: unlike LocalJSONCalendarProvider, CRUD calls themselves are
NOT wrapped in a lock -- Google's API is itself the atomic source of
truth per event, and serializing every request would just be pointless
contention. The one piece of genuinely shared, mutable client-side state
is the Credentials object and its on-disk token file: if two concurrently
-running Workers (core/react_engine.py's concurrent tool dispatch --
the exact same scenario LocalJSONCalendarProvider's own docstring calls
out) both observe an expired token at once, both would refresh and both
would write the token file, a real interleaved-write risk. _ensure_fresh_token
guards only that narrow check-refresh-persist section with a lock
(double-checked, so the common case of an already-fresh token never pays
the lock's cost).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import httpx

from tools.calendar.calendar_provider import CalendarEvent, CalendarProvider
from tools.calendar.google_auth import Credentials, Request

_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _naive_dt_to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


def _parse_google_datetime(field: dict[str, Any]) -> datetime:
    """Handles both the normal {"dateTime": "..."} shape and the all-day
    {"date": "YYYY-MM-DD"} shape (v1 never CREATES all-day events, but
    list_events/get_event must not crash on one that already exists on
    the calendar, e.g. created directly in the real Google Calendar UI --
    falls back to midnight local time)."""
    if "dateTime" in field:
        dt = datetime.fromisoformat(field["dateTime"])
        return dt.astimezone().replace(tzinfo=None)
    return datetime.combine(date.fromisoformat(field["date"]), time.min)


def _to_create_body(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "summary": event.title,
        "description": event.description,
        "start": {"dateTime": _naive_dt_to_rfc3339(event.start)},
        "end": {"dateTime": _naive_dt_to_rfc3339(event.end)},
        "extendedProperties": {"private": {"aura_important": "true" if event.important else "false"}},
    }


def _to_patch_body(patch: dict[str, Any]) -> dict[str, Any]:
    """Only includes keys actually present in `patch` -- this is what
    makes sending it via PATCH (not PUT) safe; anything omitted here is
    simply left untouched on Google's side."""
    body: dict[str, Any] = {}
    if "title" in patch:
        body["summary"] = patch["title"]
    if "description" in patch:
        body["description"] = patch["description"]
    if "start" in patch:
        body["start"] = {"dateTime": _naive_dt_to_rfc3339(datetime.fromisoformat(patch["start"]))}
    if "end" in patch:
        body["end"] = {"dateTime": _naive_dt_to_rfc3339(datetime.fromisoformat(patch["end"]))}
    if "important" in patch:
        body["extendedProperties"] = {"private": {"aura_important": "true" if patch["important"] else "false"}}
    return body


def _to_event(raw: dict[str, Any]) -> CalendarEvent:
    important = raw.get("extendedProperties", {}).get("private", {}).get("aura_important") == "true"
    return CalendarEvent(
        event_id=raw["id"],
        title=raw.get("summary", ""),
        start=_parse_google_datetime(raw["start"]),
        end=_parse_google_datetime(raw["end"]),
        important=important,
        description=raw.get("description", ""),
    )


class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, credentials: Credentials, token_file: Path, http_client: httpx.AsyncClient) -> None:
        self._credentials = credentials
        self._token_file = token_file
        self._http = http_client
        self._cred_lock = asyncio.Lock()

    async def _ensure_fresh_token(self) -> None:
        if not self._credentials.expired:
            return
        async with self._cred_lock:
            if not self._credentials.expired:  # double-checked -- another
                return                          # caller may have just refreshed
            await asyncio.to_thread(self._credentials.refresh, Request())
            self._token_file.write_text(self._credentials.to_json(), encoding="utf-8")

    async def _headers(self) -> dict[str, str]:
        await self._ensure_fresh_token()
        return {
            "Authorization": f"Bearer {self._credentials.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def list_events(self, start: date, end: date) -> list[CalendarEvent]:
        time_min = _naive_dt_to_rfc3339(datetime.combine(start, time.min))
        time_max = _naive_dt_to_rfc3339(datetime.combine(end, time.max))
        headers = await self._headers()
        raw_items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "250",
            }
            if page_token:
                params["pageToken"] = page_token
            response = await self._http.get(_BASE, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            raw_items.extend(item for item in data.get("items", []) if "dateTime" in item.get("start", {}) or "date" in item.get("start", {}))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        events = [_to_event(raw) for raw in raw_items]
        # Google's timeMin/timeMax is "overlaps the window" -- filter down
        # to LocalJSONCalendarProvider's exact semantics ("event's OWN
        # start date falls in [start, end]") so both backends behave
        # identically.
        in_range = [e for e in events if start <= e.start.date() <= end]
        return sorted(in_range, key=lambda e: e.start)

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        headers = await self._headers()
        response = await self._http.get(f"{_BASE}/{event_id}", headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _to_event(response.json())

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        headers = await self._headers()
        response = await self._http.post(_BASE, json=_to_create_body(event), headers=headers)
        if response.status_code == 409:
            raise ValueError(f"Event id '{event.event_id}' already exists")
        response.raise_for_status()
        return _to_event(response.json())

    async def update_event(self, event_id: str, patch: dict[str, Any]) -> CalendarEvent:
        headers = await self._headers()
        response = await self._http.patch(f"{_BASE}/{event_id}", json=_to_patch_body(patch), headers=headers)
        if response.status_code == 404:
            raise KeyError(f"Event id '{event_id}' not found")
        response.raise_for_status()
        return _to_event(response.json())

    async def delete_event(self, event_id: str) -> None:
        headers = await self._headers()
        response = await self._http.delete(f"{_BASE}/{event_id}", headers=headers)
        if response.status_code == 404:
            raise KeyError(f"Event id '{event_id}' not found")
        response.raise_for_status()
