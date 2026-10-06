"""CLIContext — bundles the objects cli/commands.py's slash-command
handlers need, all of which main.py already builds for the REPL/engines
anyway. A plain dataclass rather than passing a dozen positional
parameters into dispatch_command() — commands genuinely need this much
because /agents add and /skills load|install replay the same
construct + hot-register + persist steps main.py's own startup does.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agents.agent_registry import AgentRegistry
from agents.scoped_tool_registry import ScopedToolRegistryView
from config.settings import Settings
from core.logger import AuraLogger
from core.react_engine import AsyncReActEngine
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tools.calendar.swappable_calendar_provider import SwappableCalendarProvider
from tools.devices.device_store import DeviceStore
from tools.tasks.swappable_task_provider import SwappableTaskProvider
from tools.personal_graph.graph_store import GraphStore
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
from tools.scheduler.schedule_store import ScheduleStore
from tools.workspace_root import SwappableWorkspaceRoot


@dataclass
class CLIContext:
    settings: Settings
    registry: ToolRegistry
    agent_registry: AgentRegistry
    leader_view: ScopedToolRegistryView
    provider: SwappableProvider
    logger: AuraLogger
    max_turns: int
    skill_loader: SkillLoader
    mcp_manager: MCPClientManager
    http_client: httpx.AsyncClient
    agents_config_lock: asyncio.Lock
    #: The directory every workspace-scoped tool (tools/files/,
    #: tools/system/screenshot_tool.py, download_file, Skill subprocesses,
    #: tools/documents/) actually reads from RIGHT NOW -- recomputed fresh
    #: at the start of every turn by cli/service.py::sync_active_directory()
    #: (Project directory > a Chat's own directory override > the
    #: persisted default below), never mutated directly by /workspace set.
    workspace_root: SwappableWorkspaceRoot
    #: The persisted fallback /workspace set writes to -- only actually
    #: used (via sync_active_directory()) when no Project and no per-Chat
    #: directory override govern the turn. Kept separate from
    #: workspace_root itself so /workspace set can never clobber a Project/
    #: Chat directory that's currently governing a turn -- see
    #: sync_active_directory()'s own docstring for the bug this replaces.
    default_workspace_root: SwappableWorkspaceRoot
    #: Same indirection, independent instance, for tools/notes/ — /notes set
    #: repoints it (e.g. at a real Obsidian vault) without affecting workspace_root.
    notes_root: SwappableWorkspaceRoot
    #: N14 (Auralis / remote access) -- which subdirectory UNDER notes_root
    #: holds daily note files (e.g. "Daily Notes", matching Obsidian's Thino
    #: plugin convention) that quick_note_sync (gui/server.py) appends into.
    #: A plain relative-path string, not a SwappableWorkspaceRoot of its own
    #: -- resolved against notes_root.current at use time via
    #: resolve_within_sandbox(), same boundary as every other notes tool.
    #: Set via /notes quickdir <path> (cli/commands.py).
    quick_notes_subdir: str
    #: Same indirection pattern one level down — /calendar connect hot-swaps
    #: this to a real GoogleCalendarProvider once OAuth succeeds.
    calendar_provider: SwappableCalendarProvider
    #: Same indirection, independent instance, for tasks — /tasks connect
    #: hot-swaps this to a real GoogleTaskProvider once OAuth succeeds.
    task_provider: SwappableTaskProvider
    #: Registry + per-project summaries for /project. active_project is the
    #: session-only (never persisted to .env) pointer to which project (if
    #: any) is currently entered — see cli/service.py's use_project()/
    #: exit_project().
    project_store: ProjectStore
    active_project: ActiveProjectState
    #: The Leader's real engine instance and its pre-project system prompt
    #: (leader.system_prompt + user_profile text, computed once at startup).
    #: /project use/none directly mutate leader_engine.system_prompt to
    #: base_leader_system_prompt + the active project's summary (or just the
    #: base, with none active) — safe because AsyncReActEngine.run() reads
    #: self.system_prompt fresh every turn (core/react_engine.py), so this
    #: takes effect starting the very next message, no restart needed.
    leader_engine: AsyncReActEngine
    base_leader_system_prompt: str
    #: N8 proactivity (tools/scheduler/) -- backs /schedule (cli/commands.py).
    schedule_store: ScheduleStore
    #: N14 (Auralis / remote access) -- backs /device (cli/commands.py) and
    #: gui/auth.py's token checks. Always constructed (see
    #: core/bootstrap.py), regardless of whether settings.require_auth is
    #: on -- the store itself has no side effect until something actually
    #: queries it.
    device_store: DeviceStore
    #: Auralis personal-data graph (footprints/persons/projects/links) --
    #: a shared object reference like device_store above, safe on both
    #: CLIContext and AppContext (unlike quick_notes_subdir, a plain str).
    graph_store: GraphStore
    #: {"anthropic": "<key>", "openai": "<key>"} — the source of truth for
    #: which key /config use switches to, kept in sync with .env by
    #: /config set-key. Populated from Settings at startup so a key already
    #: in .env before this process started is usable immediately too.
    known_api_keys: dict[str, str] = field(default_factory=dict)
    #: Where /config set-key writes — a field (not a hardcoded PROJECT_ROOT
    #: constant inside cli/commands.py) so tests can point it at a tmp_path
    #: file instead of ever touching the real .env.
    env_file_path: Path = field(default_factory=lambda: Path(".env"))
