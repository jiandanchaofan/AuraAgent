"""ProjectStore — persistence for the "Project" concept: a named,
long-lived accumulation container the user creates at runtime (e.g. "AI
governance research", "Enoch's education") and can enter/leave/switch
between across sessions via /project (cli/commands.py).

A Project is deliberately just two things:
  - a real directory (either auto-created under settings.projects_dir, or
    an existing directory the user already has organized elsewhere) --
    entering a Project repoints the SAME SwappableWorkspaceRoot every
    other directory-scoped tool already uses (tools/files/, screenshots,
    downloads, Skill subprocesses), so "drop a document into this
    Project" is just... putting a file in that folder. No project-aware
    file-storage tool exists or is needed.
  - a compact, bounded summary -- NOT stored inside the Project's own
    directory (that stays pure user/model content, nothing of AuraAgent's
    own bookkeeping mixed in), kept instead under settings.project_meta_dir,
    mirroring the config/ (app state) vs sandbox/ (user content) split
    already used elsewhere in this project.

Project is deliberately Leader-only and tool-agnostic: it doesn't know
about notes/calendar/tasks, and Worker engines are never made aware a
Project exists at all (see core/bootstrap.py's wiring for how the active
Project's summary gets spliced into ONLY the Leader engine's system_prompt).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.react_engine import AsyncReActEngine

_SLUG_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_EMPTY_SUMMARY: dict[str, Any] = {"current_state": "", "outputs": [], "open_questions": []}


@dataclass
class ProjectInfo:
    slug: str
    name: str
    directory: Path
    created_at: str


@dataclass
class ProjectSummary:
    #: A short, standing description of where things are -- REPLACED
    #: wholesale on each update, never appended to. This is what keeps
    #: the summary's token cost roughly constant no matter how long a
    #: Project has been used, instead of growing like a log.
    current_state: str = ""
    #: "<file/deliverable> -- one-line note", dedup-appended -- lets the
    #: Leader know what already exists without re-reading it.
    outputs: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.current_state or self.outputs or self.open_questions)

    def render(self) -> str:
        """Mirrors tools/profile/user_profile_store.py::UserProfile.render() --
        "" for an empty summary costs zero extra prompt tokens rather than
        an empty-looking section every turn."""
        if self.is_empty():
            return ""
        lines = ["Active project summary (from prior sessions on this project):"]
        if self.current_state:
            lines.append(f"- Current state: {self.current_state}")
        if self.outputs:
            lines.append(f"- Outputs so far: {'; '.join(self.outputs)}")
        if self.open_questions:
            lines.append(f"- Open questions: {'; '.join(self.open_questions)}")
        return "\n".join(lines)


class ActiveProjectState:
    """Session-only, in-memory, never persisted -- selecting a Project is
    deliberately NOT written to .env the way /workspace set or /calendar
    connect are (see project_store.py's module docstring / the plan this
    was built from): the user explicitly wants this to be a frequent,
    optional, per-session choice, not a standing setting a restart
    remembers. `pre_project_workspace` is a single snapshot (not a
    stack) taken the first time ANY project is entered this session --
    switching directly between two projects doesn't touch it; only
    `/project none` consumes and clears it."""

    def __init__(self) -> None:
        self.current_slug: str | None = None
        self.pre_project_workspace: Path | None = None


class ProjectStore:
    def __init__(
        self, registry_path: Path, meta_dir: Path, summary_max_chars: int, max_list_items: int = 30
    ) -> None:
        self._registry_path = registry_path
        self._meta_dir = meta_dir
        self._summary_max_chars = summary_max_chars
        self._max_list_items = max_list_items
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._registry_path.exists():
            self._registry_path.write_text("[]", encoding="utf-8")
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _load_registry(self) -> list[dict[str, Any]]:
        return json.loads(self._registry_path.read_text(encoding="utf-8"))

    def _save_registry(self, raw: list[dict[str, Any]]) -> None:
        self._registry_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _to_info(raw: dict[str, Any]) -> ProjectInfo:
        return ProjectInfo(
            slug=raw["slug"], name=raw["name"], directory=Path(raw["directory"]), created_at=raw["created_at"]
        )

    def _summary_file(self, slug: str) -> Path:
        return self._meta_dir / slug / "summary.json"

    def _load_summary(self, slug: str) -> ProjectSummary:
        summary_file = self._summary_file(slug)
        if not summary_file.exists():
            return ProjectSummary()
        raw = {**_EMPTY_SUMMARY, **json.loads(summary_file.read_text(encoding="utf-8"))}
        return ProjectSummary(
            current_state=raw["current_state"], outputs=list(raw["outputs"]), open_questions=list(raw["open_questions"])
        )

    async def list_projects(self) -> list[ProjectInfo]:
        async with self._lock:
            return [self._to_info(raw) for raw in self._load_registry()]

    async def get_project(self, slug: str) -> ProjectInfo | None:
        async with self._lock:
            for raw in self._load_registry():
                if raw["slug"] == slug:
                    return self._to_info(raw)
            return None

    async def create_project(self, slug: str, name: str, directory: Path) -> ProjectInfo:
        """`directory` is resolved and created by the caller (cli/service.py)
        before this is called -- validation (Windows system dirs, AuraAgent's
        own project dir) lives there, reusing the exact same checks
        set_workspace_root() already established, not duplicated here."""
        if not _SLUG_PATTERN.match(slug):
            raise ValueError(f"Invalid project slug '{slug}': must match {_SLUG_PATTERN.pattern!r}.")
        async with self._lock:
            raw_projects = self._load_registry()
            if any(raw["slug"] == slug for raw in raw_projects):
                raise ValueError(f"A project named '{slug}' already exists.")
            info = ProjectInfo(
                slug=slug, name=name, directory=directory,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            raw_projects.append(
                {"slug": info.slug, "name": info.name, "directory": str(info.directory), "created_at": info.created_at}
            )
            self._save_registry(raw_projects)
            return info

    async def get_summary(self, slug: str) -> ProjectSummary:
        async with self._lock:
            return self._load_summary(slug)

    async def update_summary(
        self,
        slug: str,
        current_state: str | None = None,
        outputs: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> ProjectSummary:
        async with self._lock:
            summary_file = self._summary_file(slug)
            summary_file.parent.mkdir(parents=True, exist_ok=True)
            current = self._load_summary(slug)
            if current_state is not None:
                current.current_state = current_state[: self._summary_max_chars]
            current.outputs = _merge_dedup_capped(current.outputs, outputs, self._max_list_items)
            current.open_questions = _merge_dedup_capped(current.open_questions, open_questions, self._max_list_items)
            summary_file.write_text(
                json.dumps(
                    {
                        "current_state": current.current_state,
                        "outputs": current.outputs,
                        "open_questions": current.open_questions,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return current


def _merge_dedup_capped(existing: list[str], new_items: list[str] | None, cap: int) -> list[str]:
    if not new_items:
        return existing
    merged = list(existing)
    for item in new_items:
        if item not in merged:
            merged.append(item)
    # Keep the MOST RECENT entries when over cap -- oldest are dropped
    # first, since recency is what's most likely to still be relevant.
    return merged[-cap:] if len(merged) > cap else merged


async def sync_leader_system_prompt(
    leader_engine: AsyncReActEngine,
    base_leader_system_prompt: str,
    store: ProjectStore,
    active: ActiveProjectState,
) -> None:
    """Rewrites leader_engine.system_prompt to base + the active project's
    (if any) rendered summary. Called from two places -- /project use/none
    (cli/service.py) and update_project_summary (project_tool.py) -- so a
    mid-session summary update takes effect starting the very next turn,
    not just when a project is first entered. Safe to call this often: it's
    a plain string assignment, and AsyncReActEngine.run() (core/react_engine.py)
    reads self.system_prompt fresh on every turn, never caching it."""
    if active.current_slug is None:
        leader_engine.system_prompt = base_leader_system_prompt
        return
    summary_text = (await store.get_summary(active.current_slug)).render()
    leader_engine.system_prompt = (
        f"{base_leader_system_prompt}\n\n{summary_text}" if summary_text else base_leader_system_prompt
    )
