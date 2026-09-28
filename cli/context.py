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
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
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
    #: Shared indirection /workspace set (cli/commands.py) mutates so every
    #: workspace-scoped tool (tools/files/, tools/system/screenshot_tool.py,
    #: download_file, Skill subprocesses) picks up the new root immediately.
    workspace_root: SwappableWorkspaceRoot
    #: Same indirection, independent instance, for tools/notes/ — /notes set
    #: repoints it (e.g. at a real Obsidian vault) without affecting workspace_root.
    notes_root: SwappableWorkspaceRoot
    #: Same indirection pattern one level down — /calendar connect hot-swaps
    #: this to a real GoogleCalendarProvider once OAuth succeeds.
    calendar_provider: SwappableCalendarProvider
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
    #: {"anthropic": "<key>", "openai": "<key>"} — the source of truth for
    #: which key /config use switches to, kept in sync with .env by
    #: /config set-key. Populated from Settings at startup so a key already
    #: in .env before this process started is usable immediately too.
    known_api_keys: dict[str, str] = field(default_factory=dict)
    #: Where /config set-key writes — a field (not a hardcoded PROJECT_ROOT
    #: constant inside cli/commands.py) so tests can point it at a tmp_path
    #: file instead of ever touching the real .env.
    env_file_path: Path = field(default_factory=lambda: Path(".env"))
