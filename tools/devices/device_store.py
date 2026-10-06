"""DeviceStore -- persistence for devices allowed to talk to the GUI
backend remotely (N14: Auralis, or any other client reached through a
Cloudflare Tunnel rather than localhost). Same JSON-file + asyncio.Lock
persistence pattern as ScheduleStore/ProjectStore.

Only ever consulted when `settings.require_auth` is on (see
config/settings.py and gui/auth.py) -- off by default, so a purely local
setup never touches this at all. The raw token is returned ONLY once, from
create_device(), and never persisted -- the registry stores a SHA-256
hash, same reasoning as a password: nothing on disk is useful to an
attacker who reads it, and a lost token can only be replaced (by revoking
the device and registering a new one), never recovered.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class DeviceInfo:
    id: str
    name: str
    token_hash: str
    created_at: str
    last_seen_at: str | None


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class DeviceStore:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text("[]", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self._file_path.read_text(encoding="utf-8"))

    def _save(self, raw: list[dict[str, Any]]) -> None:
        self._file_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _to_device(raw: dict[str, Any]) -> DeviceInfo:
        return DeviceInfo(**raw)

    async def list_devices(self) -> list[DeviceInfo]:
        async with self._lock:
            return [self._to_device(raw) for raw in self._load()]

    async def get_device(self, device_id: str) -> DeviceInfo | None:
        async with self._lock:
            for raw in self._load():
                if raw["id"] == device_id:
                    return self._to_device(raw)
            return None

    async def create_device(self, name: str) -> tuple[DeviceInfo, str]:
        """Returns (DeviceInfo, raw_token) -- the raw token is the ONLY
        time it's ever available; the caller (cli/commands.py's `/device
        register`) must print it immediately, there is no way to retrieve
        it again later."""
        raw_token = secrets.token_urlsafe(32)
        info = DeviceInfo(
            id=uuid.uuid4().hex[:8],
            name=name,
            token_hash=_hash_token(raw_token),
            created_at=datetime.now(timezone.utc).isoformat(),
            last_seen_at=None,
        )
        async with self._lock:
            raw_list = self._load()
            raw_list.append(asdict(info))
            self._save(raw_list)
        return info, raw_token

    async def revoke_device(self, device_id: str) -> None:
        async with self._lock:
            raw_list = self._load()
            remaining = [raw for raw in raw_list if raw["id"] != device_id]
            if len(remaining) == len(raw_list):
                raise ValueError(f"No such device '{device_id}'.")
            self._save(remaining)

    async def verify_token(self, raw_token: str) -> DeviceInfo | None:
        """Returns the matching DeviceInfo (and records last_seen_at) if
        `raw_token` hashes to a known, non-revoked device's token_hash,
        else None -- callers (gui/auth.py) treat None as "reject"."""
        target_hash = _hash_token(raw_token)
        async with self._lock:
            raw_list = self._load()
            for raw in raw_list:
                if raw["token_hash"] == target_hash:
                    raw["last_seen_at"] = datetime.now(timezone.utc).isoformat()
                    self._save(raw_list)
                    return self._to_device(raw)
            return None
