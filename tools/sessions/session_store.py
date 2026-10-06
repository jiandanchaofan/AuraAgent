"""ChatSessionStore -- persists GUI chat sessions across restarts (the CLI
deliberately doesn't use this; see gui/server.py's module docstring for why
this whole feature is GUI-only).

Same "registry.json + one file per item" split as tools/projects/
project_store.py: `registry.json` is a JSON array of {id, title, created_at,
updated_at, pinned, project_slug} (small, cheap to load whole), and each
session's own event
stream lives in its own append-only `<id>.jsonl` file (same shape as
core/logger.py's JSONLSink -- one JSON object per line, {ts, turn,
agent_name, event_type, payload}).

Two different concurrency treatments, deliberately:
  - Registry operations (list/create/rename/delete/touch) go through
    `asyncio.Lock` + whole-file read-modify-write, same as every other JSON
    store in this project (ProjectStore, MemoryStore, UserProfileStore).
  - `append_event_sync()` is plain synchronous file I/O with NO lock and NO
    `await` inside -- this is what gui/session_sink.py's LogSink.write()
    calls, and LogSink.write() is contractually synchronous (core/logger.py's
    module docstring: it's called directly from core/react_engine.py with no
    `await`). Exactly the same reasoning JSONLSink already relies on: under
    asyncio's cooperative single-threaded scheduling, a synchronous write
    with no `await` inside is already an atomic critical section -- there's
    no concurrency hazard to lock against.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TITLE = "New chat"


@dataclass
class SessionInfo:
    id: str
    title: str
    created_at: str
    updated_at: str
    pinned: bool = False
    #: Which Project (tools/projects/project_store.py) this chat belongs to,
    #: if any -- gui/server.py syncs the ACTIVE project to match whenever
    #: this chat becomes the active one (and back out when a chat with no
    #: project_slug becomes active), so the two stay in lockstep instead of
    #: this being just a display tag. Existence of the referenced project is
    #: validated one layer up (gui/routes.py, which can see both stores);
    #: this store stays decoupled from ProjectStore.
    project_slug: str | None = None
    #: This chat's own working directory, independent of project_slug --
    #: only actually used (via cli/service.py::sync_active_directory()'s
    #: priority chain) while project_slug is None; a Project's directory
    #: always outranks it. Deliberately NEVER cleared or overwritten as a
    #: side effect of project_slug changing (in either direction) -- the
    #: two fields are independently persisted, so a custom directory set
    #: before joining a Project silently resumes taking effect the moment
    #: the chat leaves it again, with no snapshot/restore bookkeeping
    #: needed (see ActiveProjectState's own docstring for the bug that
    #: kind of bookkeeping used to cause elsewhere).
    directory: str | None = None


class ChatSessionStore:
    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir
        self._registry_path = sessions_dir / "registry.json"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        if not self._registry_path.exists():
            self._registry_path.write_text("[]", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _events_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

    def _load_registry(self) -> list[dict[str, Any]]:
        return json.loads(self._registry_path.read_text(encoding="utf-8"))

    def _save_registry(self, raw: list[dict[str, Any]]) -> None:
        self._registry_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _to_info(raw: dict[str, Any]) -> SessionInfo:
        return SessionInfo(
            id=raw["id"],
            title=raw["title"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            pinned=raw.get("pinned", False),
            project_slug=raw.get("project_slug"),
            directory=raw.get("directory"),
        )

    async def list_sessions(self, project_slug: str | None = None, unaffiliated: bool = False) -> list[SessionInfo]:
        """Pinned first, most-recently-updated first within each group --
        this is also what gui/server.py uses to decide which session to
        resume when a new connection opens with none explicitly requested
        (see its module docstring). `project_slug` filters to just one
        Project's chats (the Project detail view's Chat tab); `unaffiliated`
        filters to chats with NO Project tag at all (the sidebar's own
        list -- once a chat belongs to a Project it's only ever shown
        there, not duplicated into the general list too). Both omitted,
        every chat is returned regardless of its own tag."""
        async with self._lock:
            infos = [self._to_info(raw) for raw in self._load_registry()]
            if unaffiliated:
                infos = [s for s in infos if s.project_slug is None]
            elif project_slug is not None:
                infos = [s for s in infos if s.project_slug == project_slug]
            return sorted(infos, key=lambda s: (s.pinned, s.updated_at), reverse=True)

    async def get_session(self, session_id: str) -> SessionInfo | None:
        async with self._lock:
            for raw in self._load_registry():
                if raw["id"] == session_id:
                    return self._to_info(raw)
            return None

    async def create_session(self, title: str = DEFAULT_TITLE, project_slug: str | None = None) -> SessionInfo:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            info = SessionInfo(
                id=uuid.uuid4().hex[:12], title=title, created_at=now, updated_at=now, project_slug=project_slug
            )
            raw = self._load_registry()
            raw.append(
                {
                    "id": info.id,
                    "title": info.title,
                    "created_at": info.created_at,
                    "updated_at": info.updated_at,
                    "pinned": False,
                    "project_slug": info.project_slug,
                    "directory": info.directory,
                }
            )
            self._save_registry(raw)
            self._events_path(info.id).touch()
            return info

    async def rename_session(self, session_id: str, title: str) -> SessionInfo:
        """Raises ValueError for an unknown id or an empty title -- both
        the user's own manual rename (gui/routes.py PATCH) and the
        auto-generated title after the first exchange (gui/server.py) go
        through this same path, so they can never drift into different
        validation rules."""
        title = title.strip()
        if not title:
            raise ValueError("Title cannot be empty.")
        async with self._lock:
            raw = self._load_registry()
            for entry in raw:
                if entry["id"] == session_id:
                    entry["title"] = title[:200]
                    self._save_registry(raw)
                    return self._to_info(entry)
            raise ValueError(f"No such chat session '{session_id}'.")

    async def set_pinned(self, session_id: str, pinned: bool) -> SessionInfo:
        """Raises ValueError for an unknown id."""
        async with self._lock:
            raw = self._load_registry()
            for entry in raw:
                if entry["id"] == session_id:
                    entry["pinned"] = pinned
                    self._save_registry(raw)
                    return self._to_info(entry)
            raise ValueError(f"No such chat session '{session_id}'.")

    async def set_project(self, session_id: str, project_slug: str | None) -> SessionInfo:
        """Raises ValueError for an unknown id. Whether `project_slug` (when
        not None) actually names a real project is validated by the caller
        (gui/routes.py), which has both stores -- this one stays decoupled
        from ProjectStore, same reasoning as the class docstring's note on
        `SessionInfo.project_slug`."""
        async with self._lock:
            raw = self._load_registry()
            for entry in raw:
                if entry["id"] == session_id:
                    entry["project_slug"] = project_slug
                    self._save_registry(raw)
                    return self._to_info(entry)
            raise ValueError(f"No such chat session '{session_id}'.")

    async def set_directory(self, session_id: str, directory: str | None) -> SessionInfo:
        """Raises ValueError for an unknown id. Whether `directory` (when
        not None) is actually a valid, allowed path is validated by the
        caller (gui/routes.py, reusing cli/service.py's
        _reject_if_windows_system_dir()/_reject_if_touches_project_dir()) --
        this store just persists whatever string it's given, same
        decoupled-validation split as set_project() above."""
        async with self._lock:
            raw = self._load_registry()
            for entry in raw:
                if entry["id"] == session_id:
                    entry["directory"] = directory
                    self._save_registry(raw)
                    return self._to_info(entry)
            raise ValueError(f"No such chat session '{session_id}'.")

    async def touch(self, session_id: str) -> None:
        """Bumps updated_at to now -- called after every completed turn, so
        list_sessions()'s recency order reflects actual activity, not just
        creation time."""
        async with self._lock:
            raw = self._load_registry()
            for entry in raw:
                if entry["id"] == session_id:
                    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                    self._save_registry(raw)
                    return

    async def delete_session(self, session_id: str) -> None:
        """Raises ValueError for an unknown id. Deleting the currently
        active session is rejected one layer up (gui/routes.py), not here --
        this store has no notion of "active", only gui/session_sink.py does."""
        async with self._lock:
            raw = self._load_registry()
            remaining = [entry for entry in raw if entry["id"] != session_id]
            if len(remaining) == len(raw):
                raise ValueError(f"No such chat session '{session_id}'.")
            self._save_registry(remaining)
        self._events_path(session_id).unlink(missing_ok=True)

    def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """Synchronous, unlocked read of one session's full event stream --
        used both by gui/server.py to rebuild `history` when switching to
        a session, and to push the replay back to the frontend for
        re-rendering. A concurrent in-flight append could in principle be
        missed on the very last line, same read-your-own-writes looseness
        JSONLSink's own docstring already accepts for this project's
        single-user local scale."""
        path = self._events_path(session_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def append_event_sync(self, session_id: str, event: dict[str, Any]) -> None:
        with self._events_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
