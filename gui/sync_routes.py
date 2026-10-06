"""gui/sync_routes.py — the Auralis personal-data-graph sync surface
(N14), separate from gui/routes.py's existing REST surface since this is
its own self-contained protocol, not something cli/commands.py also
needs a "/" counterpart for.

Mirrors Auralis's own sync design (`C:\\MyPython\\Auralis\\Readme.md`
section 5.4/6): REST carries the actual data, WebSocket only ever carries
a lightweight "something changed" signal (gui/server.py's
`sink.broadcast()`, already built for N14's schedule_result) -- a
reconnecting or just-polling client always re-pulls via `GET
/api/sync/changes`, never relies on having seen every signal live.

Gated by the same device-token dependency as gui/routes.py's router (see
gui/server.py's `app.include_router(sync_router, dependencies=[...])`) --
a no-op while `settings.require_auth` is off, same as everywhere else.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/sync")


class SyncOp(BaseModel):
    op_id: str
    entity: str
    entity_id: str
    op: str  # "upsert" | "delete"
    patch: dict[str, Any] = {}


class SyncPushBody(BaseModel):
    ops: list[SyncOp]


@router.post("/push")
async def push(body: SyncPushBody, request: Request):
    ctx = request.app.state.ctx
    results = []
    for sync_op in body.ops:
        result = await ctx.graph_store.apply_op(
            sync_op.op_id, sync_op.entity, sync_op.entity_id, sync_op.op, sync_op.patch, source="user"
        )
        results.append({"op_id": sync_op.op_id, **result})

    if results:
        latest_seq = await ctx.graph_store.get_latest_seq()
        sink = request.app.state.sink
        sink.broadcast(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event_type": "sync_signal",
                "payload": {"latest_seq": latest_seq},
            }
        )
    return {"results": results}


@router.get("/changes")
async def changes(request: Request, since: int = 0):
    ctx = request.app.state.ctx
    change_list, latest_seq = await ctx.graph_store.list_changes_since(since or None)
    return {"changes": change_list, "latest_seq": latest_seq}
