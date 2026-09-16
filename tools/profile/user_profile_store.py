"""UserProfileStore — a structured, ALWAYS-VISIBLE counterpart to
tools/memory/memory_store.py's pull-based facts. Where remember_fact/
recall_facts require the LLM to actively query, the profile (preferences/
habits/common_topics/notes) is rendered once at startup and baked
directly into the orchestrator's system prompt (see main.py's
`leader_system_prompt` construction and tools/profile/user_profile_tool.py) —
the LLM never needs to call anything to "see" it.

A call to update_user_profile mid-session updates the file on disk
immediately, but — by that same "read once at startup" design —  only
takes effect for the running orchestrator's system prompt starting the
next process restart. This is a deliberate simplicity trade-off, not an
oversight: it keeps AsyncReActEngine.system_prompt exactly what it has
always been, a plain string fixed for the engine's lifetime, rather than
something engines need to re-read every turn.

Same JSON-file + asyncio.Lock persistence pattern as MemoryStore
(tools/memory/memory_store.py) and the calendar/task providers.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_EMPTY_PROFILE: dict[str, Any] = {"preferences": [], "habits": [], "common_topics": [], "notes": ""}


@dataclass
class UserProfile:
    preferences: list[str] = field(default_factory=list)
    habits: list[str] = field(default_factory=list)
    common_topics: list[str] = field(default_factory=list)
    notes: str = ""

    def is_empty(self) -> bool:
        return not (self.preferences or self.habits or self.common_topics or self.notes)

    def render(self) -> str:
        """Render as a short block of text suitable for splicing into a
        system prompt. Returns "" for an empty profile, so a user with no
        recorded profile yet costs zero extra prompt tokens rather than an
        empty-looking section every turn."""
        if self.is_empty():
            return ""
        lines = ["Known user profile (learned across past conversations):"]
        if self.preferences:
            lines.append(f"- Preferences: {'; '.join(self.preferences)}")
        if self.habits:
            lines.append(f"- Habits: {'; '.join(self.habits)}")
        if self.common_topics:
            lines.append(f"- Common topics: {'; '.join(self.common_topics)}")
        if self.notes:
            lines.append(f"- Notes: {self.notes}")
        return "\n".join(lines)


class UserProfileStore:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text(json.dumps(_EMPTY_PROFILE), encoding="utf-8")
        self._lock = asyncio.Lock()

    def _load(self) -> dict[str, Any]:
        raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        return {**_EMPTY_PROFILE, **raw}

    def _save(self, raw: dict[str, Any]) -> None:
        self._file_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _to_profile(raw: dict[str, Any]) -> UserProfile:
        return UserProfile(
            preferences=list(raw["preferences"]),
            habits=list(raw["habits"]),
            common_topics=list(raw["common_topics"]),
            notes=raw["notes"],
        )

    async def get_profile(self) -> UserProfile:
        async with self._lock:
            return self._to_profile(self._load())

    async def update_profile(
        self,
        preferences: list[str] | None = None,
        habits: list[str] | None = None,
        common_topics: list[str] | None = None,
        notes: str | None = None,
    ) -> UserProfile:
        async with self._lock:
            raw = self._load()
            raw["preferences"] = _merge_dedup(raw["preferences"], preferences)
            raw["habits"] = _merge_dedup(raw["habits"], habits)
            raw["common_topics"] = _merge_dedup(raw["common_topics"], common_topics)
            if notes:
                raw["notes"] = f"{raw['notes']}\n{notes}".strip() if raw["notes"] else notes
            self._save(raw)
            return self._to_profile(raw)


def _merge_dedup(existing: list[str], new_items: list[str] | None) -> list[str]:
    if not new_items:
        return existing
    merged = list(existing)
    for item in new_items:
        if item not in merged:
            merged.append(item)
    return merged
