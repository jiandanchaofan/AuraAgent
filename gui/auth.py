"""gui/auth.py — N14 (Auralis / remote access) device token enforcement.

Off by default (`settings.require_auth`), so a purely local setup (today's
only real scenario) never exercises any of this at all -- see
config/settings.py and tools/devices/device_store.py's own docstrings for
why. Only matters once this process sits behind something like a
Cloudflare Tunnel, reachable by more than just the person sitting at this
machine.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket

from tools.devices.device_store import DeviceStore

_BEARER_PREFIX = "Bearer "


async def require_device_token(request: Request) -> None:
    """FastAPI dependency applied to the whole REST router (see
    gui/server.py's `app.include_router(api_router, dependencies=[...])`).
    A no-op whenever `settings.require_auth` is off. Otherwise requires an
    `Authorization: Bearer <token>` header matching a registered,
    non-revoked device (tools/devices/device_store.py)."""
    ctx = request.app.state.ctx
    if not ctx.settings.require_auth:
        return
    header = request.headers.get("Authorization", "")
    if not header.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    raw_token = header[len(_BEARER_PREFIX):]
    device = await ctx.device_store.verify_token(raw_token)
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked device token.")


async def check_ws_token(websocket: WebSocket, device_store: DeviceStore, require_auth: bool) -> bool:
    """Must be called BEFORE `websocket.accept()` (see gui/server.py) --
    an unauthorized socket is rejected at the handshake, never accepted
    then closed. Reads the token from a `?token=` query param rather than
    a header: browsers' native WebSocket API can't set custom headers on
    the handshake, and this keeps the same rule working for whichever
    approach an Auralis ArkTS client ends up using. Returns True if the
    connection may proceed; on False the caller must return immediately
    without ever accepting -- this has already closed the socket itself."""
    if not require_auth:
        return True
    raw_token = websocket.query_params.get("token")
    if raw_token:
        device = await device_store.verify_token(raw_token)
        if device is not None:
            return True
    await websocket.close(code=4401)
    return False
