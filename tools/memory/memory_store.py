"""MemoryStore — persistent cross-session facts, separate from both the
ReAct engine's per-run() `history` (which never survives past one REPL
turn) and the user-facing notes sandbox (tools/notes/). A "fact" is
something the LLM itself decided was worth remembering (a stated
preference, a decision, a standing detail), not user-authored content.

Storage mirrors the calendar/task providers: a flat JSON array file,
human-readable ISO-8601 timestamps. Search is a plain case-insensitive
substring match — the same logic as tools/notes/notes_tool.py's
search_notes — deliberately not a vector store, to stay dependency-light.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataclasses import dataclass


@dataclass
class Fact:
    id: str
    content: str
    created_at: str  # ISO-8601


class MemoryStore:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self._file_path.read_text(encoding="utf-8"))

    def _save(self, raw_facts: list[dict[str, Any]]) -> None:
        self._file_path.write_text(
            json.dumps(raw_facts, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _to_fact(raw: dict[str, Any]) -> Fact:
        return Fact(id=raw["id"], content=raw["content"], created_at=raw["created_at"])

    def add_fact(self, content: str) -> Fact:
        raw_facts = self._load()
        raw = {
            "id": uuid.uuid4().hex[:8],
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        raw_facts.append(raw)
        self._save(raw_facts)
        return self._to_fact(raw)

    def search_facts(self, query: str) -> list[Fact]:
        facts = [self._to_fact(raw) for raw in self._load()]
        if not query:
            return facts
        needle = query.lower()
        return [f for f in facts if needle in f.content.lower()]
