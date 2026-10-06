"""Real Google Tasks implementation of TaskProvider — talks to the Tasks
API v1 directly over the shared httpx.AsyncClient (no
google-api-python-client, same convention tools/calendar/google_calendar_provider.py
already follows; see tools/tasks/google_tasks_auth.py's docstring for why
OAuth itself still goes through Google's own library).

All reads/writes target ONE dedicated task list (`tasklist_id`, passed in
at construction) — AuraAgent's own "AuraAgent" list, found-or-created once
by ensure_aura_task_list() at connect time (cli/service.py::finish_google_tasks_connect),
never the user's other lists.

Field mapping vs. the local TaskProvider interface:
  - `title`/`notes` -- same names on both sides.
  - `done` <-> Google's `status` ("needsAction"/"completed").
  - `due` -- this interface uses a plain ISO date ("YYYY-MM-DD"); Google's
    `due` is an RFC3339 timestamp with date-only semantics (time portion is
    always midnight UTC) -- truncated on read, expanded on write.
  - `completed_at` <-> Google's `completed` (RFC3339, present only once
    status=="completed").
  - `TaskNotFoundError` is raised on a 404 from Google, same exception
    type the local provider already raises -- tools/tasks/task_tool.py
    catches this type specifically, so both backends must raise it.

Concurrency: like GoogleCalendarProvider, CRUD calls themselves are NOT
locked (Google's API is the atomic source of truth per task) -- only the
Credentials refresh-and-persist section is, guarding against two
concurrent Workers both observing an expired token and both refreshing/
writing the token file at once.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from tools.tasks.google_tasks_auth import Credentials, Request
from tools.tasks.task_provider import UNSET, Task, TaskNotFoundError, TaskProvider

_TASKLISTS_BASE = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
_TASKS_BASE = "https://tasks.googleapis.com/tasks/v1/lists"
_AURA_LIST_TITLE = "AuraAgent"


async def ensure_aura_task_list(credentials: Credentials, http_client: httpx.AsyncClient) -> str:
    """Looks up AuraAgent's dedicated task list by title (tasklists.list),
    creating it (tasklists.insert) if it doesn't exist yet. Called ONCE,
    eagerly, by cli/service.py::finish_google_tasks_connect right after
    OAuth succeeds -- not lazily on first create_task -- so a problem
    (e.g. the Tasks API not enabled yet in the user's Cloud Console
    project) surfaces immediately as a clear connect-time error. Idempotent:
    a later reconnect finds the existing list rather than creating a
    duplicate."""
    headers = {"Authorization": f"Bearer {credentials.token}", "Accept": "application/json"}
    response = await http_client.get(_TASKLISTS_BASE, headers=headers)
    response.raise_for_status()
    for item in response.json().get("items", []):
        if item["title"] == _AURA_LIST_TITLE:
            return item["id"]
    response = await http_client.post(_TASKLISTS_BASE, json={"title": _AURA_LIST_TITLE}, headers=headers)
    response.raise_for_status()
    return response.json()["id"]


def _due_to_google(due: str) -> str:
    return f"{due}T00:00:00.000Z"


def _due_from_google(due: str) -> str:
    return due.split("T", 1)[0]


def _to_task(raw: dict[str, Any]) -> Task:
    return Task(
        id=raw["id"],
        title=raw.get("title", ""),
        notes=raw.get("notes"),
        done=raw.get("status") == "completed",
        created_at=raw.get("updated", ""),
        completed_at=raw.get("completed"),
        due=_due_from_google(raw["due"]) if raw.get("due") else None,
    )


def _to_create_body(title: str, notes: str | None, due: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title}
    if notes is not None:
        body["notes"] = notes
    if due is not None:
        body["due"] = _due_to_google(due)
    return body


class GoogleTaskProvider(TaskProvider):
    def __init__(self, credentials: Credentials, token_file: Path, tasklist_id: str, http_client: httpx.AsyncClient) -> None:
        self._credentials = credentials
        self._token_file = token_file
        self._tasklist_id = tasklist_id
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

    def _base(self) -> str:
        return f"{_TASKS_BASE}/{self._tasklist_id}/tasks"

    async def list_tasks(self, include_completed: bool = True) -> list[Task]:
        headers = await self._headers()
        raw_items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {"showCompleted": "true", "showHidden": "true", "maxResults": "100"}
            if page_token:
                params["pageToken"] = page_token
            response = await self._http.get(self._base(), params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            raw_items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        tasks = [_to_task(raw) for raw in raw_items]
        if not include_completed:
            tasks = [t for t in tasks if not t.done]
        return tasks

    async def get_task(self, task_id: str) -> Task:
        headers = await self._headers()
        response = await self._http.get(f"{self._base()}/{task_id}", headers=headers)
        if response.status_code == 404:
            raise TaskNotFoundError(f"Task id '{task_id}' not found")
        response.raise_for_status()
        return _to_task(response.json())

    async def create_task(self, title: str, notes: str | None = None, due: str | None = None) -> Task:
        headers = await self._headers()
        response = await self._http.post(self._base(), json=_to_create_body(title, notes, due), headers=headers)
        response.raise_for_status()
        return _to_task(response.json())

    async def update_task(
        self, task_id: str, *, title: str | None = None, notes: Any = UNSET, due: Any = UNSET
    ) -> Task:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if notes is not UNSET:
            body["notes"] = notes
        if due is not UNSET:
            body["due"] = _due_to_google(due) if due is not None else None
        headers = await self._headers()
        response = await self._http.patch(f"{self._base()}/{task_id}", json=body, headers=headers)
        if response.status_code == 404:
            raise TaskNotFoundError(f"Task id '{task_id}' not found")
        response.raise_for_status()
        return _to_task(response.json())

    async def complete_task(self, task_id: str) -> Task:
        return await self.set_task_done(task_id, True)

    async def set_task_done(self, task_id: str, done: bool) -> Task:
        headers = await self._headers()
        body = {"status": "completed" if done else "needsAction"}
        response = await self._http.patch(f"{self._base()}/{task_id}", json=body, headers=headers)
        if response.status_code == 404:
            raise TaskNotFoundError(f"Task id '{task_id}' not found")
        response.raise_for_status()
        return _to_task(response.json())

    async def delete_task(self, task_id: str) -> None:
        headers = await self._headers()
        response = await self._http.delete(f"{self._base()}/{task_id}", headers=headers)
        if response.status_code == 404:
            raise TaskNotFoundError(f"Task id '{task_id}' not found")
        response.raise_for_status()
