"""gui/graph_routes.py — the human-facing REST surface over the personal
data graph (tools/personal_graph/graph_store.py), for the Windows GUI's own
"足迹" Tab: quick-entry, browsing, in-place editing, soft-delete/restore for
footprints/persons/projects. Deliberately separate from gui/sync_routes.py,
which is Auralis's own machine-to-machine sync protocol (push/changes) --
this module is a different, human-CRUD-shaped surface over the SAME
GraphStore, not a replacement or a fork of it.

Reuses sync_routes.py's own conventions: `ctx = request.app.state.ctx` ->
`ctx.graph_store`; the identical `sync_signal` broadcast (payload carries
only `latest_seq`, never the data itself -- Auralis's own "REST carries
data, WS carries only signals" principle) after every write, including a
restore, so any connected client re-pulls; gated by the same
`require_device_token` dependency, applied at `app.include_router(...)`
time in gui/server.py, not per-route here.

The async AI-enrichment task (`_enrich_footprint`) is this module's other
half: Option A, fire-and-forget. POST /api/graph/footprints returns
immediately after saving with a heuristic/default classification;
`asyncio.create_task` schedules the AI pass separately, which classifies
the footprint's `type` and looks for unmentioned person/project links,
writing both back with `source="ai"`. It relies entirely on
GraphStore.apply_op's own human-authority checks to no-op when it
shouldn't (never overriding a manual/tag classification, never
duplicating/reviving a link the user already owns or dismissed) --
nothing here re-implements that gating. PATCH (editing a footprint's text)
deliberately never schedules this task and never touches `type`/
`type_source` -- editing text must not retrigger AI recognition (explicit
product decision).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from uuid6 import uuid7

from core.bootstrap import AppContext
from core.message_types import ConversationTurn
from gui.ws_log_sink import WebSocketSink
from tools.personal_graph.graph_links import resolve_footprint_mentions
from tools.personal_graph.graph_store import GraphStore

router = APIRouter(prefix="/api/graph")

_TRASH_ENTITIES = {"footprint", "person", "project"}
_VALID_FOOTPRINT_TYPES = {"interpersonal", "idea", "project", "excerpt"}


async def _broadcast(request: Request) -> None:
    ctx: AppContext = request.app.state.ctx
    sink: WebSocketSink = request.app.state.sink
    latest_seq = await ctx.graph_store.get_latest_seq()
    sink.broadcast(
        {"ts": datetime.now(timezone.utc).isoformat(), "event_type": "sync_signal", "payload": {"latest_seq": latest_seq}}
    )


def _footprint_json(footprint: dict[str, Any], links: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    return {**footprint, "links": links or {"persons": [], "projects": []}}


def _person_json(person: dict[str, Any]) -> dict[str, Any]:
    return dict(person)


def _project_json(project: dict[str, Any]) -> dict[str, Any]:
    return dict(project)


# --- Footprints --------------------------------------------------------------


@router.get("/footprints")
async def list_footprints(
    request: Request,
    person_id: str | None = None,
    project_tag: str | None = None,
    keyword: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    ctx: AppContext = request.app.state.ctx
    footprints = await ctx.graph_store.list_footprints(
        person_id=person_id, project_tag=project_tag, keyword=keyword, since=since, limit=limit, offset=offset
    )
    result = []
    for footprint in footprints:
        links = await ctx.graph_store.get_footprint_links(footprint["id"])
        result.append(_footprint_json(footprint, links))
    return result


class CreateFootprintBody(BaseModel):
    text: str
    occurred_at: str | None = None
    location: str | None = None
    person_names: list[str] | None = None
    project_tag: str | None = None


@router.post("/footprints")
async def create_footprint(body: CreateFootprintBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    sink: WebSocketSink = request.app.state.sink
    footprint_id = str(uuid7())
    patch: dict[str, Any] = {
        "text": body.text,
        "occurred_at": body.occurred_at or datetime.now(timezone.utc).isoformat(),
    }
    if body.location:
        patch["location"] = body.location
    # Heuristic classification -- REST-quick-entry-only, never shared with
    # graph_tool.py::create_footprint (which deliberately never guesses a
    # type). This is the first real use of the "heuristic" priority tier.
    if body.project_tag:
        patch["type"] = "project"
        patch["type_source"] = "heuristic"
    elif body.person_names:
        patch["type"] = "interpersonal"
        patch["type_source"] = "heuristic"
    else:
        patch["type_source"] = "default"

    await ctx.graph_store.apply_op(str(uuid4()), "footprint", footprint_id, "upsert", patch, source="user")
    await resolve_footprint_mentions(ctx.graph_store, footprint_id, body.person_names, body.project_tag, source="user")
    await _broadcast(request)

    asyncio.create_task(_enrich_footprint(ctx, sink, footprint_id))

    footprint = await ctx.graph_store.get_footprint(footprint_id)
    links = await ctx.graph_store.get_footprint_links(footprint_id)
    return _footprint_json(footprint, links)


class UpdateFootprintBody(BaseModel):
    text: str | None = None
    occurred_at: str | None = None
    location: str | None = None


@router.patch("/footprints/{footprint_id}")
async def update_footprint(footprint_id: str, body: UpdateFootprintBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    # Never includes type/type_source -- apply_op's priority gate is never
    # even invoked, and _enrich_footprint is never scheduled below. This is
    # what makes "editing text doesn't retrigger AI recognition" true.
    await ctx.graph_store.apply_op(str(uuid4()), "footprint", footprint_id, "upsert", patch, source="user")
    await _broadcast(request)
    footprint = await ctx.graph_store.get_footprint(footprint_id)
    if footprint is None:
        raise HTTPException(status_code=404, detail=f"No such footprint '{footprint_id}'.")
    links = await ctx.graph_store.get_footprint_links(footprint_id)
    return _footprint_json(footprint, links)


@router.delete("/footprints/{footprint_id}")
async def delete_footprint(footprint_id: str, request: Request):
    ctx: AppContext = request.app.state.ctx
    await ctx.graph_store.apply_op(str(uuid4()), "footprint", footprint_id, "delete", {}, source="user")
    await _broadcast(request)
    return {"deleted": footprint_id}


@router.post("/links/{link_id}/dismiss")
async def dismiss_link(link_id: str, request: Request):
    ctx: AppContext = request.app.state.ctx
    result = await ctx.graph_store.apply_op(str(uuid4()), "link", link_id, "upsert", {"dismissed": 1}, source="user")
    await _broadcast(request)
    return result


# --- Persons -----------------------------------------------------------------


@router.get("/persons")
async def list_persons(request: Request, keyword: str | None = None, limit: int = 100, offset: int = 0):
    ctx: AppContext = request.app.state.ctx
    persons = await ctx.graph_store.list_persons(keyword=keyword, limit=limit, offset=offset)
    return [_person_json(p) for p in persons]


class CreatePersonBody(BaseModel):
    name: str
    aliases: list[str] | None = None
    birthday: str | None = None
    key_info: str | None = None


@router.post("/persons")
async def create_person(body: CreatePersonBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    person_id = str(uuid7())
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await ctx.graph_store.apply_op(str(uuid4()), "person", person_id, "upsert", patch, source="user")
    await _broadcast(request)
    matches = await ctx.graph_store.find_person(body.name)
    return _person_json(next(p for p in matches if p["id"] == person_id))


class UpdatePersonBody(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    birthday: str | None = None
    key_info: str | None = None


@router.patch("/persons/{person_id}")
async def update_person(person_id: str, body: UpdatePersonBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await ctx.graph_store.apply_op(str(uuid4()), "person", person_id, "upsert", patch, source="user")
    await _broadcast(request)
    persons = await ctx.graph_store.list_persons(limit=1000)
    match = next((p for p in persons if p["id"] == person_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"No such person '{person_id}'.")
    return _person_json(match)


@router.delete("/persons/{person_id}")
async def delete_person(person_id: str, request: Request):
    ctx: AppContext = request.app.state.ctx
    await ctx.graph_store.apply_op(str(uuid4()), "person", person_id, "delete", {}, source="user")
    await _broadcast(request)
    return {"deleted": person_id}


# --- Projects (Auralis's #tag concept -- unrelated to tools/projects/'s own
# "Project" accumulation-container feature) ----------------------------------


@router.get("/projects")
async def list_graph_projects(request: Request, keyword: str | None = None, limit: int = 100, offset: int = 0):
    ctx: AppContext = request.app.state.ctx
    projects = await ctx.graph_store.list_projects(keyword=keyword, limit=limit, offset=offset)
    return [_project_json(p) for p in projects]


class CreateGraphProjectBody(BaseModel):
    tag: str
    name: str | None = None
    goal: str | None = None


@router.post("/projects")
async def create_graph_project(body: CreateGraphProjectBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    project_id = str(uuid7())
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await ctx.graph_store.apply_op(str(uuid4()), "project", project_id, "upsert", patch, source="user")
    await _broadcast(request)
    project = await ctx.graph_store.get_project(body.tag)
    return _project_json(project)


class UpdateGraphProjectBody(BaseModel):
    tag: str | None = None
    name: str | None = None
    goal: str | None = None


@router.patch("/projects/{project_id}")
async def update_graph_project(project_id: str, body: UpdateGraphProjectBody, request: Request):
    ctx: AppContext = request.app.state.ctx
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await ctx.graph_store.apply_op(str(uuid4()), "project", project_id, "upsert", patch, source="user")
    await _broadcast(request)
    projects = await ctx.graph_store.list_projects(limit=1000)
    match = next((p for p in projects if p["id"] == project_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"No such project '{project_id}'.")
    return _project_json(match)


@router.delete("/projects/{project_id}")
async def delete_graph_project(project_id: str, request: Request):
    ctx: AppContext = request.app.state.ctx
    await ctx.graph_store.apply_op(str(uuid4()), "project", project_id, "delete", {}, source="user")
    await _broadcast(request)
    return {"deleted": project_id}


# --- Trash --------------------------------------------------------------------


@router.get("/trash")
async def list_trash(request: Request, entity: str, limit: int = 100):
    if entity not in _TRASH_ENTITIES:
        raise HTTPException(status_code=400, detail=f"Unknown trash entity '{entity}'.")
    ctx: AppContext = request.app.state.ctx
    rows = await ctx.graph_store.list_trash(entity, limit=limit)
    return rows


@router.post("/trash/{entity}/{entity_id}/restore")
async def restore_entity(entity: str, entity_id: str, request: Request):
    if entity not in _TRASH_ENTITIES:
        raise HTTPException(status_code=400, detail=f"Unknown trash entity '{entity}'.")
    ctx: AppContext = request.app.state.ctx
    result = await ctx.graph_store.restore_entity(str(uuid4()), entity, entity_id)
    await _broadcast(request)
    return result


# --- Async AI-enrichment background task -------------------------------------

_ENRICH_SYSTEM_PROMPT = (
    "You are classifying a single short personal journal entry (a 'footprint'). "
    "Reply with ONLY a strict JSON object, no other text, matching this shape: "
    '{"type": one of "interpersonal"|"idea"|"project"|"excerpt"|null, '
    '"person_names": [string, ...], "project_tag": string or null}. '
    "type: \"interpersonal\" if it's mainly about an interaction with someone, \"idea\" for a thought/reflection, "
    "\"project\" if it's progress on a named goal/campaign, \"excerpt\" for a quote/clipping, or null if unclear. "
    "person_names: names of people clearly involved, not already obvious filler words. "
    "project_tag: a short lowercase tag for an ongoing project/goal this clearly belongs to, or null. "
    "Omit anything you are not reasonably confident about -- an empty/null answer is better than a wrong guess."
)


async def _enrich_footprint(ctx: AppContext, sink: WebSocketSink, footprint_id: str) -> None:
    """Fire-and-forget (Option A): scheduled right after a footprint is
    saved via POST /api/graph/footprints, never awaited by the request
    that scheduled it. Never raises -- nothing is waiting on this, and a
    failure here must never surface anywhere. See module docstring."""
    try:
        footprint = await ctx.graph_store.get_footprint(footprint_id)
        if footprint is None:
            return
        existing_links = await ctx.graph_store.get_footprint_links(footprint_id)
        response = await ctx.provider.send(
            _ENRICH_SYSTEM_PROMPT, [ConversationTurn(role="user", text=footprint["text"])], []
        )
        data = json.loads((response.thought_text or "").strip())
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        return

    try:
        footprint_type = data.get("type")
        if footprint_type in _VALID_FOOTPRINT_TYPES:
            await ctx.graph_store.apply_op(
                str(uuid4()), "footprint", footprint_id, "upsert", {"type": footprint_type, "type_source": "ai"}, source="ai"
            )

        existing_person_names = {p["name"] for p in existing_links["persons"]}
        existing_project_tags = {p["tag"] for p in existing_links["projects"] if p.get("tag")}
        new_person_names = [n for n in (data.get("person_names") or []) if n not in existing_person_names]
        new_project_tag = data.get("project_tag")
        if new_project_tag in existing_project_tags:
            new_project_tag = None
        if new_person_names or new_project_tag:
            await resolve_footprint_mentions(
                ctx.graph_store, footprint_id, new_person_names, new_project_tag, source="ai"
            )
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        return

    try:
        latest_seq = await ctx.graph_store.get_latest_seq()
        sink.broadcast(
            {"ts": datetime.now(timezone.utc).isoformat(), "event_type": "sync_signal", "payload": {"latest_seq": latest_seq}}
        )
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        return
