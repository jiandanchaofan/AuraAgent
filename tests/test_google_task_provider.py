"""Tests for tools/tasks/google_task_provider.py. Uses httpx.MockTransport
(no extra mocking dependency) to fake Tasks API v1 HTTP responses -- no
real Google account or network access is used or needed. Credentials are
a lightweight fake (not a real google-auth Credentials object), same
approach as tests/test_google_calendar_provider.py.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tools.tasks.google_task_provider import GoogleTaskProvider, ensure_aura_task_list
from tools.tasks.task_provider import TaskNotFoundError


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


def _provider(tmp_path, handler, tasklist_id="list-1", credentials=None):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    credentials = credentials or FakeCredentials()
    token_file = tmp_path / "google_tasks_token.json"
    return GoogleTaskProvider(credentials, token_file, tasklist_id, http_client), credentials, token_file


@pytest.mark.asyncio
async def test_token_refresh_happens_once_for_concurrent_calls(tmp_path):
    refresh_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    credentials = FakeCredentials(expired=True)
    orig_refresh = credentials.refresh

    def counting_refresh(request):
        refresh_count["n"] += 1
        orig_refresh(request)

    credentials.refresh = counting_refresh
    provider, _, _ = _provider(tmp_path, handler, credentials=credentials)

    await asyncio.gather(provider.list_tasks(), provider.list_tasks())

    assert refresh_count["n"] == 1


@pytest.mark.asyncio
async def test_create_task_round_trips_fields(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(200, json={"id": "t1", "title": body["title"], "notes": body.get("notes"), "due": body.get("due"), "status": "needsAction"})

    provider, _, _ = _provider(tmp_path, handler)

    created = await provider.create_task("Buy milk", notes="2%", due="2026-03-05")

    assert captured["body"]["title"] == "Buy milk"
    assert captured["body"]["notes"] == "2%"
    assert captured["body"]["due"] == "2026-03-05T00:00:00.000Z"
    assert created.title == "Buy milk"
    assert created.due == "2026-03-05"
    assert created.done is False


@pytest.mark.asyncio
async def test_update_task_sends_patch_with_only_changed_fields(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1", "title": "New title", "status": "needsAction"})

    provider, _, _ = _provider(tmp_path, handler)

    await provider.update_task("t1", title="New title")

    assert captured["body"] == {"title": "New title"}
    assert "notes" not in captured["body"]
    assert "due" not in captured["body"]


@pytest.mark.asyncio
async def test_update_task_can_explicitly_clear_notes(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1", "title": "x", "status": "needsAction"})

    provider, _, _ = _provider(tmp_path, handler)

    await provider.update_task("t1", notes=None)

    assert captured["body"] == {"notes": None}


@pytest.mark.asyncio
async def test_set_task_done_toggles_both_directions(tmp_path):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"id": "t1", "title": "x", "status": body["status"]})

    provider, _, _ = _provider(tmp_path, handler)

    done_task = await provider.set_task_done("t1", True)
    not_done_task = await provider.set_task_done("t1", False)

    assert bodies[0] == {"status": "completed"}
    assert bodies[1] == {"status": "needsAction"}
    assert done_task.done is True
    assert not_done_task.done is False


@pytest.mark.asyncio
async def test_get_task_404_raises_task_not_found_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider, _, _ = _provider(tmp_path, handler)

    with pytest.raises(TaskNotFoundError):
        await provider.get_task("nonexistent")


@pytest.mark.asyncio
async def test_delete_task_404_raises_task_not_found_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider, _, _ = _provider(tmp_path, handler)

    with pytest.raises(TaskNotFoundError):
        await provider.delete_task("nonexistent")


# --- ensure_aura_task_list ---------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_aura_task_list_creates_when_missing(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "other", "title": "My Tasks"}]})
        body = json.loads(request.content)
        assert body == {"title": "AuraAgent"}
        return httpx.Response(200, json={"id": "new-list-id", "title": "AuraAgent"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    credentials = FakeCredentials()

    list_id = await ensure_aura_task_list(credentials, http_client)

    assert list_id == "new-list-id"
    assert calls == ["GET", "POST"]


@pytest.mark.asyncio
async def test_ensure_aura_task_list_is_idempotent_on_reconnect(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise AssertionError("must not create a second list when one already exists")
        return httpx.Response(200, json={"items": [{"id": "existing-id", "title": "AuraAgent"}]})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    credentials = FakeCredentials()

    list_id = await ensure_aura_task_list(credentials, http_client)

    assert list_id == "existing-id"
