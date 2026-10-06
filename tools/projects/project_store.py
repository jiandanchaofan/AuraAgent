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

Two further pieces of accumulated context, added for the "sustainable
accumulation base" upgrade, follow the SAME push-vs-pull split already
established for the global user_profile (push) vs memory_store (pull):
  - `ProjectSummary.role` -- a short persona/instructions block, PUSHED
    into the Leader's system prompt via render(), same mechanism as
    current_state. AI-drafted (update_project_summary) but freely
    user-editable (PATCH /api/projects/{slug}/role, /project role).
  - Per-project Memory -- PULLED via remember_project_fact/
    recall_project_facts (tools/projects/project_tool.py), never
    auto-injected, because facts can accumulate past what's worth paying
    prompt-token cost for on every turn. Backed by memory_store_for()
    below, which just points the EXISTING MemoryStore class
    (tools/memory/memory_store.py) at this project's own facts.json --
    no new store class.
`ProjectInfo.enabled_tools` is a third, unrelated piece: Skill/MCP tool
name patterns visible to the Leader ONLY while this project is active
(see agents/scoped_tool_registry.py's dynamic-patterns mechanism) --
purely additive on top of the always-on config/agents.json capabilities.
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
from tools.memory.memory_store import MemoryStore

_SLUG_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_EMPTY_SUMMARY: dict[str, Any] = {"role": "", "current_state": "", "open_questions": []}


@dataclass
class ProjectInfo:
    slug: str
    name: str
    directory: Path
    created_at: str
    #: Tool name patterns (fnmatch globs, e.g. "mcp_filesystem_*") that
    #: become visible to the Leader ONLY while this project is active --
    #: see ScopedToolRegistryView.set_dynamic_patterns() and
    #: cli/service.py's use_project()/exit_project(). Purely additive on
    #: top of the orchestrator's always-on config/agents.json capabilities,
    #: never a restriction.
    enabled_tools: list[str] = field(default_factory=list)


@dataclass
class ProjectSummary:
    #: A short persona/instructions block for how the Leader should act
    #: within this project -- AI-drafted (via update_project_summary) but
    #: freely user-editable (PATCH /api/projects/{slug}/role, /project
    #: role). REPLACED wholesale on each update, same as current_state,
    #: and shares its character cap -- Role is meant to stay short.
    role: str = ""
    #: A short, standing description of where things are -- REPLACED
    #: wholesale on each update, never appended to. This is what keeps
    #: the summary's token cost roughly constant no matter how long a
    #: Project has been used, instead of growing like a log. Deliberately
    #: also the place to mention a notable deliverable in passing (e.g.
    #: "drafted report.pdf, now working on slides") -- there used to be a
    #: separate `outputs` list for this, but it was pure duplication of
    #: what `list_directory` already answers accurately and on demand, so
    #: it was retired: any AI-maintained catalog of "files that exist" can
    #: only ever be a stale shadow of the real directory, never the
    #: source of truth. See docs/ARCHITECTURE.md's Epic P2 entry.
    current_state: str = ""
    open_questions: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.role or self.current_state or self.open_questions)

    def render(self) -> str:
        """Mirrors tools/profile/user_profile_store.py::UserProfile.render() --
        "" for an empty summary costs zero extra prompt tokens rather than
        an empty-looking section every turn. `role`, if set, comes first --
        persona before status, so the Leader knows WHO it's acting as
        before reading WHERE things stand."""
        if self.is_empty():
            return ""
        lines = ["Active project summary (from prior sessions on this project):"]
        if self.role:
            lines.append(f"- Role: {self.role}")
        if self.current_state:
            lines.append(f"- Current state: {self.current_state}")
        if self.open_questions:
            lines.append(f"- Open questions: {'; '.join(self.open_questions)}")
        return "\n".join(lines)


class ActiveProjectState:
    """Session-only, in-memory, never persisted -- selecting a Project is
    deliberately NOT written to .env the way /workspace set or /calendar
    connect are (see project_store.py's module docstring / the plan this
    was built from): the user explicitly wants this to be a frequent,
    optional, per-session choice, not a standing setting a restart
    remembers.

    Deliberately holds ONLY `current_slug`, no snapshot of "what
    workspace_root was before this project" -- an earlier version had a
    `pre_project_workspace` field for exactly that, consumed/cleared by
    /project none to restore it. That snapshot-and-restore approach is
    what let /workspace set and /project use silently clobber each other
    (set_workspace_root() had no idea a snapshot even existed, so it would
    overwrite workspace_root while a project was active, and the next
    /project none would then "restore" a value that was never meant to be
    live anymore). cli/service.py::sync_active_directory() replaces the
    whole snapshot/restore model: every turn recomputes its own correct
    directory fresh (Project > a Chat's own override > the persisted
    default), so there is nothing to remember or put back -- see that
    function's own docstring."""

    def __init__(self) -> None:
        self.current_slug: str | None = None


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
            slug=raw["slug"],
            name=raw["name"],
            directory=Path(raw["directory"]),
            created_at=raw["created_at"],
            enabled_tools=list(raw.get("enabled_tools", [])),
        )

    def _summary_file(self, slug: str) -> Path:
        return self._meta_dir / slug / "summary.json"

    def _memory_file(self, slug: str) -> Path:
        return self._meta_dir / slug / "memory.json"

    def memory_store_for(self, slug: str) -> MemoryStore:
        """A per-Project instance of the SAME MemoryStore class the global
        remember_fact/recall_facts tools use, just pointed at this
        Project's own facts.json instead of the global one -- see the
        module docstring's "Memory reuses MemoryStore" note. Constructed
        fresh on each call (cheap: MemoryStore's __init__ just mkdir's and
        seeds an empty file if missing) rather than cached, so callers
        never need to worry about holding a stale instance across a
        Project rename/directory change."""
        return MemoryStore(self._memory_file(slug))

    def _load_summary(self, slug: str) -> ProjectSummary:
        summary_file = self._summary_file(slug)
        if not summary_file.exists():
            return ProjectSummary()
        # {**_EMPTY_SUMMARY, **raw} also harmlessly absorbs a stray "outputs"
        # key from a summary.json written before that field was retired --
        # it's simply never read back into ProjectSummary below, and drops
        # out of the file for good the next time this project is saved.
        raw = {**_EMPTY_SUMMARY, **json.loads(summary_file.read_text(encoding="utf-8"))}
        return ProjectSummary(
            role=raw["role"],
            current_state=raw["current_state"],
            open_questions=list(raw["open_questions"]),
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
                {
                    "slug": info.slug,
                    "name": info.name,
                    "directory": str(info.directory),
                    "created_at": info.created_at,
                    "enabled_tools": [],
                }
            )
            self._save_registry(raw_projects)
            return info

    async def rename_project(self, slug: str, name: str) -> ProjectInfo:
        """Renames the project's display `name` -- `slug` (its id and
        directory-mapping key) never changes. Same validation shape as
        ChatSessionStore.rename_session() (tools/sessions/session_store.py),
        which both the CLI ("/project rename", cli/commands.py) and the GUI
        (PATCH /api/projects/{slug}, gui/routes.py) go through."""
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        async with self._lock:
            raw_projects = self._load_registry()
            for raw in raw_projects:
                if raw["slug"] == slug:
                    raw["name"] = name[:200]
                    self._save_registry(raw_projects)
                    return self._to_info(raw)
            raise ValueError(f"No project named '{slug}'.")

    async def set_enabled_tools(self, slug: str, patterns: list[str]) -> ProjectInfo:
        """Replaces this Project's `enabled_tools` wholesale (not merged) --
        same shape as rename_project. Called from cli/service.py's
        set_project_tools(), which also re-syncs ScopedToolRegistryView's
        dynamic patterns immediately if `slug` happens to be the currently
        active project (see agents/scoped_tool_registry.py)."""
        async with self._lock:
            raw_projects = self._load_registry()
            for raw in raw_projects:
                if raw["slug"] == slug:
                    raw["enabled_tools"] = list(patterns)
                    self._save_registry(raw_projects)
                    return self._to_info(raw)
            raise ValueError(f"No project named '{slug}'.")

    async def get_summary(self, slug: str) -> ProjectSummary:
        async with self._lock:
            return self._load_summary(slug)

    async def update_summary(
        self,
        slug: str,
        role: str | None = None,
        current_state: str | None = None,
        open_questions: list[str] | None = None,
    ) -> ProjectSummary:
        async with self._lock:
            summary_file = self._summary_file(slug)
            summary_file.parent.mkdir(parents=True, exist_ok=True)
            current = self._load_summary(slug)
            if role is not None:
                current.role = role[: self._summary_max_chars]
            if current_state is not None:
                current.current_state = current_state[: self._summary_max_chars]
            current.open_questions = _merge_dedup_capped(current.open_questions, open_questions, self._max_list_items)
            summary_file.write_text(
                json.dumps(
                    {
                        "role": current.role,
                        "current_state": current.current_state,
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
