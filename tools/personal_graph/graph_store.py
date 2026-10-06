"""GraphStore — the relational (SQLite) store for Auralis's personal data
model: footprints (life-event atoms), persons, projects, and the links
between them. See `C:\\MyPython\\Auralis\\Readme.md` (sections 2/3/6) for the
authoritative entity model and sync contract this mirrors.

Why SQLite, not another JSON-file store (unlike every other `*_store.py`
in this project): this data is genuinely relational -- "every footprint
linked to this person" is a join, not a list scan, and `links` is itself
an entity (source/confidence/dismissed), not a bare foreign key. Plain
stdlib `sqlite3`, called synchronously from inside `async def` methods
under `self._lock` -- the same convention every other store here already
uses for its own (synchronous) file I/O, not a deviation.

id generation is symmetric, not Auralis-only: UUIDv7 is designed for
multiple independent generators to never collide (millisecond timestamp +
random bits, no coordination needed) and sorts by creation time. Whichever
side creates a new entity generates its own id at creation time -- this
store never generates one itself, and never assumes an id it's given
originated on the phone. `tools/scheduler/schedule_store.py`/
`tools/devices/device_store.py`'s short `uuid.uuid4().hex[:8]` ids are a
DIFFERENT, unrelated convention (AuraAgent-internal entities that never
need to line up with another device's id space) -- don't reuse that
pattern here.

"人工权威" (human authority) enforcement lives HERE, not just as a
convention the caller is trusted to honor -- same "the boundary lives on
the server side, not the caller" principle as
tools/sandbox_path.py::resolve_within_sandbox(). See `apply_op`'s own
docstring for the exact rules.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS footprints (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    polished_text TEXT,
    occurred_at TEXT,
    location TEXT,
    image_path TEXT,
    type TEXT,
    type_source TEXT,
    enrich_state TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT,
    birthday TEXT,
    key_info TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS circles (
    id TEXT PRIMARY KEY,
    name TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS person_circles (
    person_id TEXT NOT NULL,
    circle_id TEXT NOT NULL,
    PRIMARY KEY (person_id, circle_id)
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    tag TEXT UNIQUE,
    name TEXT,
    goal TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE,
    type_hint TEXT,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS footprint_tags (
    footprint_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (footprint_id, tag_id)
);
CREATE TABLE IF NOT EXISTS links (
    id TEXT PRIMARY KEY,
    from_entity TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_entity TEXT NOT NULL,
    to_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS intel_cards (
    id TEXT PRIMARY KEY,
    title TEXT,
    body TEXT,
    kind TEXT,
    received_at TEXT,
    state TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    kind TEXT,
    period_start TEXT,
    content TEXT,
    generated_at TEXT
);
CREATE TABLE IF NOT EXISTS change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    op TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_ops (
    op_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

#: Auralis Readme.md section 3.1's five-tier priority chain -- SMALLER
#: number wins. A write may only overwrite an existing `type_source` when
#: its own priority number is <= the existing value's (equal allows a
#: later same-tier pass to refine an earlier one, e.g. a second `ai` pass).
_TYPE_SOURCE_PRIORITY = {"manual": 1, "tag": 2, "ai": 3, "heuristic": 4, "default": 5}

#: Wire/outbox entity names (Auralis Readme.md section 6.1, singular) ->
#: this store's table names (plural, conventional SQL naming).
_ENTITY_TABLES = {
    "footprint": "footprints",
    "person": "persons",
    "project": "projects",
    "tag": "tags",
    "link": "links",
}

#: Patch fields accepted per entity -- anything else in a patch is
#: ignored, not an error (forward-compatible with fields this version
#: doesn't know about yet).
_ENTITY_COLUMNS = {
    "footprint": ["text", "polished_text", "occurred_at", "location", "image_path", "type", "type_source", "enrich_state"],
    "person": ["name", "aliases", "birthday", "key_info"],
    "project": ["tag", "name", "goal"],
    "tag": ["name", "type_hint"],
    "link": ["from_entity", "from_id", "to_entity", "to_id", "source", "confidence", "dismissed"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    def _get_row(self, table: str, entity_id: str) -> sqlite3.Row | None:
        return self._conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()

    def _record_change(self, entity: str, entity_id: str, op: str, row: sqlite3.Row | None) -> None:
        snapshot = dict(row) if row is not None else {"id": entity_id}
        self._conn.execute(
            "INSERT INTO change_log (entity, entity_id, op, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
            # ensure_ascii=False for the same reason as the aliases encoding
            # above -- round-trips correctly either way via json.loads, but
            # keeps non-Latin text readable if the raw DB file is ever
            # inspected directly.
            (entity, entity_id, op, json.dumps(snapshot, default=str, ensure_ascii=False), _now()),
        )

    def _mark_applied(self, op_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO applied_ops (op_id, applied_at) VALUES (?, ?)", (op_id, _now())
        )

    async def apply_op(
        self, op_id: str, entity: str, entity_id: str, op: str, patch: dict[str, Any], source: str
    ) -> dict[str, Any]:
        """The one write path for every entity table -- a sync push from
        Auralis and a locally-originated write (e.g. create_footprint)
        both go through this, so change_log/applied_ops bookkeeping and
        the human-authority checks below are never bypassed by either
        origin.

        `op_id` makes this idempotent: replaying the same op_id (a retried
        push) returns `{"status": "duplicate"}` without writing again.

        Human-authority rules enforced HERE (Auralis Readme.md section
        3.1/6.2), not left to the caller to honor:
        - A `footprint.type`/`type_source` update is only applied if the
          new source's priority number is <= the existing row's (see
          `_TYPE_SOURCE_PRIORITY`) -- e.g. `ai` (3) may overwrite
          `heuristic`/`default` (4/5) but never `manual`/`tag` (1/2).
          Blocked updates return `{"status": "skipped", "reason": ...}`.
        - A `link` is rejected if another link already exists at the same
          (from_entity, from_id, to_entity, to_id) with `source="user"`
          or `dismissed=1` -- a human's own link, or a suggestion they
          already dismissed, is never silently joined by a new AI one.
        """
        async with self._lock:
            if self._conn.execute("SELECT 1 FROM applied_ops WHERE op_id = ?", (op_id,)).fetchone():
                return {"status": "duplicate", "version": None}

            table = _ENTITY_TABLES.get(entity)
            if table is None:
                raise ValueError(f"Unknown entity '{entity}'")

            if op == "delete":
                existing = self._get_row(table, entity_id)
                new_version = (existing["version"] + 1) if existing is not None else 0
                self._conn.execute(
                    f"UPDATE {table} SET deleted_at = ?, version = ? WHERE id = ?", (_now(), new_version, entity_id)
                )
                self._record_change(entity, entity_id, "delete", self._get_row(table, entity_id))
                self._mark_applied(op_id)
                self._conn.commit()
                return {"status": "ok", "version": new_version}

            if op != "upsert":
                raise ValueError(f"Unknown op '{op}'")

            existing = self._get_row(table, entity_id)

            if entity == "footprint" and existing is not None and "type_source" in patch:
                existing_priority = _TYPE_SOURCE_PRIORITY.get(existing["type_source"], 5)
                new_priority = _TYPE_SOURCE_PRIORITY.get(patch.get("type_source"), 5)
                if new_priority > existing_priority:
                    self._mark_applied(op_id)
                    self._conn.commit()
                    return {"status": "skipped", "version": existing["version"], "reason": "type_source priority"}

            if entity == "link" and source == "ai":
                conflict = self._conn.execute(
                    "SELECT 1 FROM links WHERE from_entity=? AND from_id=? AND to_entity=? AND to_id=?"
                    " AND (source='user' OR dismissed=1) AND id != ?",
                    (patch.get("from_entity"), patch.get("from_id"), patch.get("to_entity"), patch.get("to_id"), entity_id),
                ).fetchone()
                if conflict:
                    self._mark_applied(op_id)
                    self._conn.commit()
                    return {"status": "skipped", "version": None, "reason": "existing user link or dismissed"}

            values: dict[str, Any] = {col: patch[col] for col in _ENTITY_COLUMNS[entity] if col in patch}
            if entity == "person" and isinstance(values.get("aliases"), list):
                # ensure_ascii=False -- a non-Latin alias (e.g. "小明") must be
                # stored as its real UTF-8 text, not a \uXXXX escape, since
                # find_person() matches aliases with a plain SQL LIKE substring
                # search against the raw stored string.
                values["aliases"] = json.dumps(values["aliases"], ensure_ascii=False)

            if existing is None:
                values["id"] = entity_id
                values["version"] = 0
                columns = ", ".join(values.keys())
                placeholders = ", ".join("?" for _ in values)
                self._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))
                new_version = 0
            else:
                new_version = existing["version"] + 1
                set_clause = ", ".join(f"{col} = ?" for col in values)
                params = [*values.values(), new_version, entity_id]
                self._conn.execute(f"UPDATE {table} SET {set_clause}, version = ? WHERE id = ?", params)

            self._record_change(entity, entity_id, "upsert", self._get_row(table, entity_id))
            self._mark_applied(op_id)
            self._conn.commit()
            return {"status": "ok", "version": new_version}

    async def get_latest_seq(self) -> int:
        async with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS latest FROM change_log").fetchone()
        return row["latest"]

    async def list_changes_since(self, since_seq: int | None) -> tuple[list[dict[str, Any]], int]:
        async with self._lock:
            if since_seq is None:
                rows = self._conn.execute("SELECT * FROM change_log ORDER BY seq ASC").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM change_log WHERE seq > ? ORDER BY seq ASC", (since_seq,)
                ).fetchall()
            latest_row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS latest FROM change_log").fetchone()
        changes = [
            {
                "seq": row["seq"],
                "entity": row["entity"],
                "entity_id": row["entity_id"],
                "op": row["op"],
                "patch": json.loads(row["snapshot_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return changes, latest_row["latest"]

    @staticmethod
    def _decode_person(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.get("aliases"):
            try:
                result["aliases"] = json.loads(result["aliases"])
            except (TypeError, ValueError):
                pass
        return result

    async def find_person(self, name_or_alias: str) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM persons WHERE deleted_at IS NULL AND (name LIKE ? OR aliases LIKE ?)",
                (f"%{name_or_alias}%", f"%{name_or_alias}%"),
            ).fetchall()
        return [self._decode_person(r) for r in rows]

    async def get_project(self, tag: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE tag = ? AND deleted_at IS NULL", (tag,)
            ).fetchone()
        return dict(row) if row is not None else None

    async def get_footprint(self, footprint_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM footprints WHERE id = ? AND deleted_at IS NULL", (footprint_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    async def list_persons(self, keyword: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        query = "SELECT * FROM persons WHERE deleted_at IS NULL"
        params: list[Any] = []
        if keyword:
            query += " AND (name LIKE ? OR aliases LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        query += " ORDER BY name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._decode_person(r) for r in rows]

    async def list_projects(self, keyword: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        query = "SELECT * FROM projects WHERE deleted_at IS NULL"
        params: list[Any] = []
        if keyword:
            query += " AND (tag LIKE ? OR name LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        query += " ORDER BY tag ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    async def list_trash(self, entity: str, limit: int = 100) -> list[dict[str, Any]]:
        table = _ENTITY_TABLES.get(entity)
        if table is None or entity not in ("footprint", "person", "project"):
            raise ValueError(f"Unknown trash entity '{entity}'")
        async with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM {table} WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?", (limit,)
            ).fetchall()
        if entity == "person":
            return [self._decode_person(r) for r in rows]
        return [dict(r) for r in rows]

    async def get_footprint_links(self, footprint_id: str) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:
            person_rows = self._conn.execute(
                "SELECT l.id AS link_id, l.source AS link_source, p.* FROM links l"
                " JOIN persons p ON p.id = l.to_id AND p.deleted_at IS NULL"
                " WHERE l.from_entity='footprint' AND l.from_id=? AND l.to_entity='person'"
                " AND l.dismissed=0 AND l.deleted_at IS NULL",
                (footprint_id,),
            ).fetchall()
            project_rows = self._conn.execute(
                "SELECT l.id AS link_id, l.source AS link_source, pr.* FROM links l"
                " JOIN projects pr ON pr.id = l.to_id AND pr.deleted_at IS NULL"
                " WHERE l.from_entity='footprint' AND l.from_id=? AND l.to_entity='project'"
                " AND l.dismissed=0 AND l.deleted_at IS NULL",
                (footprint_id,),
            ).fetchall()
        return {
            "persons": [dict(r) for r in person_rows],
            "projects": [dict(r) for r in project_rows],
        }

    async def restore_entity(self, op_id: str, entity: str, entity_id: str) -> dict[str, Any]:
        """A sibling of `apply_op`, not a change to its own `upsert` path --
        clearing `deleted_at` implicitly on upsert would alter already-tested
        sync semantics for what happens when Auralis pushes an upsert onto a
        locally soft-deleted row. Mirrors apply_op's own idempotency/version/
        change_log shape, with a distinct `op="restore"` in change_log so a
        reader can tell "this came back" apart from "this is new data"."""
        table = _ENTITY_TABLES.get(entity)
        if table is None:
            raise ValueError(f"Unknown entity '{entity}'")
        async with self._lock:
            if self._conn.execute("SELECT 1 FROM applied_ops WHERE op_id = ?", (op_id,)).fetchone():
                return {"status": "duplicate", "version": None}
            existing = self._get_row(table, entity_id)
            if existing is None:
                self._mark_applied(op_id)
                self._conn.commit()
                return {"status": "not_found", "version": None}
            if existing["deleted_at"] is None:
                self._mark_applied(op_id)
                self._conn.commit()
                return {"status": "not_deleted", "version": existing["version"]}
            new_version = existing["version"] + 1
            self._conn.execute(
                f"UPDATE {table} SET deleted_at = NULL, version = ? WHERE id = ?", (new_version, entity_id)
            )
            self._record_change(entity, entity_id, "restore", self._get_row(table, entity_id))
            self._mark_applied(op_id)
            self._conn.commit()
            return {"status": "ok", "version": new_version}

    async def list_footprints(
        self,
        person_id: str | None = None,
        project_tag: str | None = None,
        keyword: str | None = None,
        since: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT DISTINCT f.* FROM footprints f"
        conditions = ["f.deleted_at IS NULL"]
        params: list[Any] = []
        if person_id:
            query += (
                " JOIN links lp ON lp.from_entity='footprint' AND lp.from_id=f.id"
                " AND lp.to_entity='person' AND lp.to_id=? AND lp.deleted_at IS NULL"
            )
            params.append(person_id)
        if project_tag:
            query += (
                " JOIN links lpr ON lpr.from_entity='footprint' AND lpr.from_id=f.id"
                " AND lpr.to_entity='project' AND lpr.deleted_at IS NULL"
                " JOIN projects p ON p.id = lpr.to_id AND p.tag = ?"
            )
            params.append(project_tag)
        if keyword:
            conditions.append("f.text LIKE ?")
            params.append(f"%{keyword}%")
        if since:
            conditions.append("f.occurred_at >= ?")
            params.append(since)
        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY f.occurred_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
