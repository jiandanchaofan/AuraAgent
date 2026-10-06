"""cli/service.py — the shared logic behind BOTH the "/" command layer
(cli/commands.py) and the GUI's REST endpoints (gui/routes.py, Epic M3).
This module is the actual convergence point M3 is about: a query or
action defined here is called by both frontends, so a GUI panel and its
"/" command counterpart can never drift into different behavior — the
same architectural principle core/bootstrap.py already applies one layer
up (shared team/tool construction), applied here to the command layer.

Every function takes already-known values and does the work; none of them
call input()/getpass()/print() themselves. Collecting a human's answer (a
terminal prompt, a form submit) is entirely the caller's job — cli/commands.py
prompts via input()/getpass() and prints the result, gui/routes.py reads
an HTTP request body and returns JSON. Failures are reported the normal
Python way (raising AgentDefinitionError/AgentRegistryError/ValueError/
SkillPackageError/etc, or letting an httpx exception propagate) rather
than a return-code/message pair, so each caller can format the SAME
failure however fits its own medium (a printed line vs. an HTTP error
body) without this module needing to know which.
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from dotenv import set_key

from agents.agent_builder import build_worker, ensure_worker_name_available
from agents.agent_config_writer import add_agent_entry, add_capability, remove_agent_entry
from agents.agent_definition import AgentDefinition
from cli.context import CLIContext
from config.settings import PROJECT_ROOT
from core.exceptions import SandboxPathError
from core.message_types import ConversationTurn
from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from skills.skill_package import StagedSkillInstall, extract_skill_files, finalize_skill_install, stage_skill_install
from tools.calendar.google_auth import load_credentials as load_google_credentials
from tools.calendar.google_calendar_provider import GoogleCalendarProvider
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.devices.device_store import DeviceInfo
from tools.memory.memory_store import Fact
from tools.projects.project_store import ProjectInfo, ProjectSummary, sync_leader_system_prompt
from tools.sandbox_path import resolve_within_sandbox
from tools.scheduler.schedule_store import UNSET, ScheduledTask
from tools.tasks.google_task_provider import GoogleTaskProvider, ensure_aura_task_list
from tools.tasks.google_tasks_auth import load_credentials as load_google_tasks_credentials
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider

KNOWN_PROVIDERS = ("anthropic", "openai")
_ENV_KEY_BY_PROVIDER = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


# --- config ------------------------------------------------------------------


@dataclass
class ConfigStatus:
    provider: str
    model: str
    key_masked: str


def mask_key(key: str) -> str:
    if len(key) <= 4:
        return "(not set)" if not key else "***"
    return f"{key[:2]}...{key[-2:]}"


def get_config_status(ctx: CLIContext) -> ConfigStatus:
    name = ctx.provider.provider_name
    return ConfigStatus(provider=name, model=ctx.provider.model_name, key_masked=mask_key(ctx.known_api_keys.get(name, "")))


def switch_provider(provider_name: str, model_id: str, ctx: CLIContext) -> None:
    """Raises ValueError, with a message safe to show a human directly, if
    the provider is unknown or no key is on file for it yet."""
    if provider_name not in KNOWN_PROVIDERS:
        raise ValueError(f"Unknown provider '{provider_name}' — expected 'anthropic' or 'openai'.")
    api_key = ctx.known_api_keys.get(provider_name, "")
    if not api_key:
        raise ValueError(f"No API key known for '{provider_name}' yet — set one first.")

    new_provider: Any
    if provider_name == "anthropic":
        new_provider = AnthropicProvider(api_key=api_key, model=model_id, max_tokens=ctx.settings.llm_max_output_tokens)
    else:
        new_provider = OpenAIProvider(
            api_key=api_key,
            model=model_id,
            base_url=ctx.settings.openai_base_url,
            max_tokens=ctx.settings.llm_max_output_tokens,
        )
    ctx.provider.set_current(new_provider, provider_name)


def set_api_key(provider_name: str, value: str, ctx: CLIContext) -> None:
    """Raises ValueError for an unknown provider or an empty value. Never
    logs or otherwise echoes `value` -- writes straight to ctx.known_api_keys
    (in-memory) and ctx.env_file_path (via python-dotenv's set_key)."""
    if provider_name not in KNOWN_PROVIDERS:
        raise ValueError(f"Unknown provider '{provider_name}' — expected 'anthropic' or 'openai'.")
    value = value.strip()
    if not value:
        raise ValueError("Empty key — nothing changed.")

    ctx.known_api_keys[provider_name] = value
    set_key(str(ctx.env_file_path), _ENV_KEY_BY_PROVIDER[provider_name], value)


# --- agents --------------------------------------------------------------


@dataclass
class AgentSummary:
    name: str
    role: str
    capabilities: list[str]


def list_agents(ctx: CLIContext) -> list[AgentSummary]:
    leader = ctx.agent_registry.leader
    summaries = [AgentSummary(name=leader.name, role="leader", capabilities=list(leader.capabilities))]
    summaries += [AgentSummary(name=w.name, role="worker", capabilities=list(w.capabilities)) for w in ctx.agent_registry.workers]
    return summaries


def validate_new_agent(name: str, system_prompt: str, capabilities: list[str], ctx: CLIContext) -> AgentDefinition:
    """Raises AgentDefinitionError (bad name/empty prompt/no capabilities)
    or ValueError (name collision with an existing agent or tool)."""
    agent_def = AgentDefinition(name=name, role="worker", system_prompt=system_prompt, capabilities=capabilities)
    ensure_worker_name_available(name, ctx.agent_registry, ctx.registry)
    return agent_def


async def commit_new_agent(agent_def: AgentDefinition, ctx: CLIContext) -> None:
    """Hot-registers `agent_def` (already validated by validate_new_agent)
    as a delegate_to_<name> tool on the Leader's own view, and persists it
    to config/agents.json so it's still there after a restart."""
    delegate_tool = build_worker(agent_def, ctx.registry, ctx.provider, ctx.logger, ctx.max_turns)
    ctx.leader_view.add_extra_tool(delegate_tool)
    ctx.agent_registry.add_worker(agent_def)
    await add_agent_entry(
        ctx.settings.agents_config_path,
        {
            "name": agent_def.name,
            "role": "worker",
            "system_prompt": agent_def.system_prompt,
            "capabilities": agent_def.capabilities,
        },
        ctx.agents_config_lock,
    )


async def remove_agent(name: str, ctx: CLIContext) -> None:
    """Raises AgentRegistryError (unknown name, or an attempt to remove the
    leader). The registry check happens first, before any side effect, so
    a rejected removal leaves everything untouched."""
    ctx.agent_registry.remove_worker(name)
    ctx.leader_view.remove_extra_tool(f"delegate_to_{name}")
    await remove_agent_entry(ctx.settings.agents_config_path, name, ctx.agents_config_lock)


# --- skills --------------------------------------------------------------


@dataclass
class SkillSummary:
    name: str
    description: str


def list_skills(ctx: CLIContext) -> list[SkillSummary]:
    specs_by_name = {s.name: s for s in ctx.registry.get_tool_specs()}
    result = []
    for name in ctx.skill_loader.registered_skill_names:
        spec = specs_by_name.get(name)
        result.append(SkillSummary(name=name, description=spec.description if spec else "(unknown)"))
    return result


def stage_skill_from_bytes(data: bytes, ctx: CLIContext) -> StagedSkillInstall:
    """Raises SkillPackageError (bad zip, bad structure, name collision) or
    SkillManifestError (bad SKILL.md). Nothing touches disk yet."""
    skill_md_text, run_py_text = extract_skill_files(data)
    return stage_skill_install(skill_md_text, run_py_text, ctx.settings.skills_dir, ctx.registry)


def stage_skill_from_path(local_path: str, ctx: CLIContext) -> StagedSkillInstall:
    """Raises FileNotFoundError if `local_path` doesn't exist."""
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"No such file: '{local_path}'")
    return stage_skill_from_bytes(path.read_bytes(), ctx)


async def stage_skill_from_url(url: str, ctx: CLIContext) -> StagedSkillInstall:
    """Raises ValueError for a non-http(s) URL, before any network call; an
    httpx exception propagates as-is for a download/HTTP-status failure."""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("Only http:// and https:// URLs are supported.")
    response = await ctx.http_client.get(url, timeout=15.0)
    response.raise_for_status()
    return stage_skill_from_bytes(response.content, ctx)


async def commit_skill(staged: StagedSkillInstall, ctx: CLIContext) -> str:
    """After a human has reviewed and approved `staged`: writes it to disk,
    hot-registers it, and persists the capability grant. Returns the
    registered tool name."""
    registered_name = finalize_skill_install(staged, ctx.skill_loader)
    ctx.leader_view.add_allowed_pattern(registered_name)
    await add_capability(ctx.settings.agents_config_path, ctx.agent_registry.leader.name, registered_name, ctx.agents_config_lock)
    return registered_name


# --- mcp -------------------------------------------------------------------


@dataclass
class McpServerStatus:
    name: str
    tool_count: int


def list_mcp_servers(ctx: CLIContext) -> list[McpServerStatus]:
    """Read-only status -- adding a new MCP server still only happens via
    propose_mcp_server (LLM-proposed, human-approved) or hand-editing
    config/mcp_servers.json + a restart; there's no direct human-initiated
    install path the way /skills load|install gives Skills."""
    specs = ctx.registry.get_tool_specs()
    result = []
    for server_name in ctx.mcp_manager.connected_servers:
        prefix = f"mcp_{server_name}_"
        tool_count = sum(1 for s in specs if s.name.startswith(prefix))
        result.append(McpServerStatus(name=server_name, tool_count=tool_count))
    return result


# --- workspace -------------------------------------------------------------


@dataclass
class WorkspaceStatus:
    #: The directory actually in effect for this turn right now -- may be
    #: a Project's own directory (or, in the GUI, a Chat's own override),
    #: not necessarily `default`.
    path: Path
    #: The persisted fallback /workspace set writes to -- what a NEW,
    #: unaffiliated chat (or the CLI with no project active) falls back
    #: to. Equal to `path` whenever no Project/Chat directory currently
    #: governs.
    default: Path


def get_workspace_status(ctx: CLIContext) -> WorkspaceStatus:
    return WorkspaceStatus(path=ctx.workspace_root.current, default=ctx.default_workspace_root.current)


@dataclass
class WorkspaceRootUpdate:
    default: Path
    #: True when a Project is currently active, meaning this new default
    #: was saved but does NOT take effect until /project none.
    deferred: bool


def _windows_blocked_subtree_roots() -> set[Path]:
    """Windows install dir + Program Files (both) -- setting the workspace
    to one of these OR a subdirectory of one would let every
    workspace-scoped tool read/write real system files. Silently empty on
    non-Windows (the env vars are never set there)."""
    candidates = [os.environ.get(name) for name in ("WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)")]
    return {Path(c).resolve() for c in candidates if c}


def _windows_drive_roots() -> set[Path]:
    """Bare drive roots (C:\\, D:\\, ...) -- unlike the system directories
    above, only the EXACT drive root itself is rejected, never a
    subdirectory of it (a drive root is an ancestor of literally every
    path on that drive, so treating it like the subtree roots above would
    reject almost any real directory on a single-drive machine)."""
    if os.name != "nt":
        return set()
    return {Path(f"{letter}:\\").resolve() for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()}


def reject_if_windows_system_dir(path: Path) -> None:
    if path in _windows_drive_roots():
        raise ValueError(f"'{path}' is a drive root and cannot be used as the workspace.")
    for root in _windows_blocked_subtree_roots():
        if path == root or root in path.parents:
            raise ValueError(f"'{path}' is a Windows system directory (or inside one: '{root}') and cannot be used as the workspace.")


def reject_if_touches_project_dir(path: Path) -> None:
    """Blocks both directions: pointing INTO AuraAgent's own project
    directory (its source/config/.env would become reachable through the
    file tools) and pointing at something that CONTAINS it (same result,
    just from the other side)."""
    project_root = PROJECT_ROOT.resolve()
    if path == project_root or project_root in path.parents or path in project_root.parents:
        raise ValueError(f"'{path}' is (or contains, or is contained by) AuraAgent's own project directory ('{project_root}') and cannot be used as the workspace.")


async def set_workspace_root(new_path: str, ctx: CLIContext) -> WorkspaceRootUpdate:
    """Validates and persists a new DEFAULT workspace directory (not
    necessarily the live one right now -- see WorkspaceRootUpdate/
    ctx.default_workspace_root's own docstrings). Persists to .env the
    same way set_api_key() does, so it survives a restart. Raises
    ValueError with a human-readable reason for any rejected path -- a
    Windows system directory, AuraAgent's own project directory, an
    existing non-directory path, or (real bug this caught) a drive letter
    that doesn't exist at all: Path.mkdir(parents=True) can only create
    directories on an already-mounted volume, so pointing at a nonexistent
    drive (e.g. 'D:\\...' on a machine with no D: drive) raises a raw
    FileNotFoundError -- WinError 3 -- rather than the clean,
    human-readable rejection every other invalid path gets here.

    If no Project is currently active, the new default also takes effect
    immediately (matching the old, pre-refactor behavior for the common
    case) via sync_active_directory(); if a Project IS active, the save
    still succeeds (this is a real, useful setting for later), but does
    NOT touch the live directory -- a Project always outranks the
    default, by design (see sync_active_directory()'s own docstring) --
    the caller should tell the human it's deferred (`.deferred` on the
    result) rather than silently doing nothing."""
    resolved = Path(new_path).expanduser().resolve()
    reject_if_windows_system_dir(resolved)
    reject_if_touches_project_dir(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"'{resolved}' already exists and is not a directory.")

    try:
        ctx.default_workspace_root.set_current(resolved)  # creates the directory if needed
    except OSError as exc:
        raise ValueError(f"Could not create or access '{resolved}': {exc}") from exc
    set_key(str(ctx.env_file_path), "AURA_WORKSPACE_ROOT", str(resolved))

    deferred = ctx.active_project.current_slug is not None
    if not deferred:
        await sync_active_directory(ctx, project_slug=None)
    return WorkspaceRootUpdate(default=resolved, deferred=deferred)


# --- notes -------------------------------------------------------------


@dataclass
class NotesRootStatus:
    path: Path
    #: N14 (Auralis / remote access) -- see CLIContext.quick_notes_subdir's
    #: own docstring. A relative sub-path under `path` above, not a second
    #: standalone root.
    quick_notes_subdir: str


def get_notes_root_status(ctx: CLIContext) -> NotesRootStatus:
    return NotesRootStatus(path=ctx.notes_root.current, quick_notes_subdir=ctx.quick_notes_subdir)


def set_notes_root(new_path: str, ctx: CLIContext) -> Path:
    """Same validation/persistence as set_workspace_root() above, applied to
    the independent notes sandbox instead -- e.g. pointing it at a real
    Obsidian vault so search_notes/read_note/create_note/update_note read
    and write real notes instead of the repo-local sandbox default."""
    resolved = Path(new_path).expanduser().resolve()
    reject_if_windows_system_dir(resolved)
    reject_if_touches_project_dir(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"'{resolved}' already exists and is not a directory.")

    try:
        ctx.notes_root.set_current(resolved)  # creates the directory if needed
    except OSError as exc:
        raise ValueError(f"Could not create or access '{resolved}': {exc}") from exc
    set_key(str(ctx.env_file_path), "AURA_NOTES_SANDBOX_ROOT", str(resolved))
    return resolved


def set_quick_notes_subdir(new_subdir: str, ctx: CLIContext) -> str:
    """N14 (Auralis / remote access): which subdirectory under notes_root
    holds the daily note files quick_note_sync (gui/server.py) appends
    into -- e.g. 'Daily Notes', matching Obsidian's Thino plugin
    convention. Validated the same way a tool call argument would be
    (resolve_within_sandbox against the CURRENT notes_root), so a bad value
    is caught now rather than failing later on first use -- but stored as
    the relative string itself, not the resolved absolute path, since
    notes_root can itself be repointed later (/notes set) and this should
    keep following it."""
    new_subdir = new_subdir.strip().strip("/\\")
    if not new_subdir:
        raise ValueError("Quick notes subdirectory cannot be empty.")
    try:
        resolve_within_sandbox(ctx.notes_root.current, new_subdir)
    except SandboxPathError as exc:
        raise ValueError(str(exc)) from exc
    ctx.quick_notes_subdir = new_subdir
    set_key(str(ctx.env_file_path), "AURA_QUICK_NOTES_SUBDIR", new_subdir)
    return new_subdir


# --- calendar ----------------------------------------------------------


@dataclass
class CalendarStatus:
    backend: str


def get_calendar_status(ctx: CLIContext) -> CalendarStatus:
    return CalendarStatus(backend=ctx.calendar_provider.backend_name)


def check_google_client_secret(ctx: CLIContext) -> None:
    """Raises ValueError with step-by-step setup instructions if the OAuth
    client secret file (downloaded by the human from Google Cloud Console --
    this project cannot automate creating that project/OAuth client) isn't
    in place yet. Called BEFORE cli/commands.py runs the actual (blocking,
    browser-opening) OAuth flow, so a missing prerequisite is reported
    without ever opening a browser."""
    if not ctx.settings.google_client_secret_file.exists():
        raise ValueError(
            "No Google OAuth client secret found. To connect Google Calendar:\n"
            "  1. Go to https://console.cloud.google.com/ and create (or pick) a project.\n"
            "  2. Enable the 'Google Calendar API' for that project.\n"
            "  3. Create an OAuth 2.0 Client ID of type 'Desktop app'.\n"
            "  4. Download its JSON and save it as:\n"
            f"     {ctx.settings.google_client_secret_file}\n"
            "  5. Run /calendar connect again."
        )


def finish_google_calendar_connect(ctx: CLIContext) -> None:
    """Called by cli/commands.py AFTER google_auth.run_oauth_flow() has
    already completed successfully (the token file now exists on disk).
    Loads the fresh credentials, hot-swaps ctx.calendar_provider to a real
    GoogleCalendarProvider (takes effect immediately, no restart needed),
    and persists AURA_CALENDAR_BACKEND=google to .env the same way
    set_api_key()/set_workspace_root() already do -- so a future restart
    picks 'google' back up via load_settings()."""
    credentials = load_google_credentials(ctx.settings.google_token_file)
    if credentials is None:
        raise ValueError(f"Expected a token at '{ctx.settings.google_token_file}' but none was found.")
    new_provider = GoogleCalendarProvider(credentials, ctx.settings.google_token_file, ctx.http_client)
    ctx.calendar_provider.set_current(new_provider, "google")
    set_key(str(ctx.env_file_path), "AURA_CALENDAR_BACKEND", "google")


def disconnect_google_calendar(ctx: CLIContext) -> None:
    """Switches back to the local JSON calendar immediately and persists
    AURA_CALENDAR_BACKEND=local. Deliberately does NOT delete the saved
    Google token file -- run_oauth_flow() requests access_type='offline'
    + prompt='consent', which is what guarantees a refresh_token is
    issued, so a later /calendar connect can reuse it without the user
    having to click through the browser consent screen again."""
    new_provider = LocalJSONCalendarProvider(ctx.settings.calendar_events_file)
    ctx.calendar_provider.set_current(new_provider, "local")
    set_key(str(ctx.env_file_path), "AURA_CALENDAR_BACKEND", "local")


# --- tasks -----------------------------------------------------------------


@dataclass
class TaskBackendStatus:
    backend: str


def get_task_backend_status(ctx: CLIContext) -> TaskBackendStatus:
    return TaskBackendStatus(backend=ctx.task_provider.backend_name)


def check_google_tasks_client_secret(ctx: CLIContext) -> None:
    """Same structure as check_google_client_secret -- checks the SAME
    ctx.settings.google_client_secret_file (Tasks reuses Calendar's
    registered OAuth Desktop-app Client ID), just with Tasks-specific
    wording. Called BEFORE the actual (blocking, browser-opening) OAuth
    flow, so a missing prerequisite is reported without ever opening a
    browser."""
    if not ctx.settings.google_client_secret_file.exists():
        raise ValueError(
            "No Google OAuth client secret found. To connect Google Tasks:\n"
            "  1. Go to https://console.cloud.google.com/ and create (or pick) a project.\n"
            "  2. Enable the 'Google Tasks API' for that project.\n"
            "  3. Create an OAuth 2.0 Client ID of type 'Desktop app' (or reuse the one\n"
            "     already set up for Google Calendar, if any).\n"
            "  4. Download its JSON and save it as:\n"
            f"     {ctx.settings.google_client_secret_file}\n"
            "  5. Run /tasks connect again."
        )


async def finish_google_tasks_connect(ctx: CLIContext) -> None:
    """Called by cli/commands.py AFTER google_tasks_auth.run_oauth_flow()
    has already completed successfully (the token file now exists on
    disk). Unlike finish_google_calendar_connect, this is async: it makes
    a real HTTP call (ensure_aura_task_list) to find-or-create AuraAgent's
    dedicated task list BEFORE hot-swapping the provider in, so a problem
    (e.g. the Tasks API not enabled yet) is reported clearly at connect
    time rather than on some later, unrelated action."""
    credentials = load_google_tasks_credentials(ctx.settings.google_tasks_token_file)
    if credentials is None:
        raise ValueError(f"Expected a token at '{ctx.settings.google_tasks_token_file}' but none was found.")
    list_id = await ensure_aura_task_list(credentials, ctx.http_client)
    new_provider = GoogleTaskProvider(credentials, ctx.settings.google_tasks_token_file, list_id, ctx.http_client)
    ctx.task_provider.set_current(new_provider, "google")
    set_key(str(ctx.env_file_path), "AURA_TASKS_BACKEND", "google")
    set_key(str(ctx.env_file_path), "AURA_GOOGLE_TASKS_LIST_ID", list_id)
    ctx.settings.google_tasks_list_id = list_id  # keep in-memory Settings in sync with .env


def disconnect_google_tasks(ctx: CLIContext) -> None:
    """Mirrors disconnect_google_calendar -- switches back to the local
    JSON task list immediately and persists AURA_TASKS_BACKEND=local.
    Deliberately does NOT delete the saved Google Tasks token file, same
    reasoning as the Calendar equivalent (a later /tasks connect can reuse
    it without a fresh consent screen)."""
    new_provider = LocalJSONTaskProvider(ctx.settings.tasks_file)
    ctx.task_provider.set_current(new_provider, "local")
    set_key(str(ctx.env_file_path), "AURA_TASKS_BACKEND", "local")


# --- projects ------------------------------------------------------------


@dataclass
class ProjectStatus:
    active_slug: str | None
    projects: list[ProjectInfo]


async def get_project_status(ctx: CLIContext) -> ProjectStatus:
    projects = await ctx.project_store.list_projects()
    return ProjectStatus(active_slug=ctx.active_project.current_slug, projects=projects)


async def rename_project(ctx: CLIContext, slug: str, name: str) -> ProjectInfo:
    """Raises ValueError for an unknown slug or an empty name. Renames the
    display name only -- `slug` (the id / directory-mapping key) never
    changes, same split as tools/sessions/session_store.py's rename."""
    return await ctx.project_store.rename_project(slug, name)


async def create_project(ctx: CLIContext, slug: str, directory: str | None = None) -> ProjectInfo:
    """Raises ValueError for an invalid/duplicate slug, or (when `directory`
    points at an existing path the caller wants to adopt rather than
    auto-create one) the same Windows-system-dir/AuraAgent-own-project-dir
    rejections set_workspace_root() already established -- reused here,
    not duplicated, since a project's directory becomes workspace_root
    too once /project use enters it."""
    if directory:
        resolved = Path(directory).expanduser().resolve()
        reject_if_windows_system_dir(resolved)
        reject_if_touches_project_dir(resolved)
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"'{resolved}' already exists and is not a directory.")
    else:
        resolved = (ctx.settings.projects_dir / slug).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return await ctx.project_store.create_project(slug, slug, resolved)


async def sync_active_directory(
    ctx: CLIContext, *, project_slug: str | None, directory_override: str | None = None,
) -> None:
    """The one place that decides which real directory a turn should
    operate in, and makes it so. Called by /workspace set, /project use/
    none, and -- at the start of every single turn, not just when a chat
    is switched to -- gui/server.py's _run_orchestrator() and
    tools/scheduler/scheduler_loop.py, so a GUI turn's directory can never
    be left stale by whatever ran before it (a scheduled run, a different
    chat on the same connection). The CLI's REPL (main.py) does NOT call
    this per turn -- it has no per-chat state to re-resolve from, so
    /workspace set and /project use/none already apply directly and
    immediately, and scheduler_loop.py explicitly restores whatever was
    active before its own run once it's done (see that module's own
    docstring for why the CLI specifically still needs that restore).

    Priority: `project_slug` (a Project's own directory) > `directory_override`
    (a GUI chat's own per-chat directory setting) > `ctx.default_workspace_root`
    (the persisted fallback /workspace set writes to). Also re-syncs
    active_project.current_slug, the Leader's dynamic tool patterns (a
    Project's enabled_tools), its system prompt, and -- via
    MCPClientManager.sync_directory_following_servers() -- reconnects any
    MCP server configured to follow the active directory
    (config/mcp_servers.json's follow_active_directory). Every dependent
    piece of state is a pure function of "which project_slug/
    directory_override govern this specific turn," never a value some
    earlier, unrelated call left behind and forgot to restore -- see
    ActiveProjectState's own docstring for the bug this replaces
    (/workspace set and /project use used to silently clobber each
    other's state).

    Raises ValueError if `project_slug` names a project that doesn't
    exist.

    Typed as `ctx: CLIContext`, but only ever does attribute access, never
    an isinstance() check -- tools/scheduler/scheduler_loop.py calls this
    with an `AppContext` instead (which carries every one of the same
    field names directly), the same structural-typing approach
    core/react_engine.py's own module docstring already documents for
    ToolRegistry/ScopedToolRegistryView."""
    dynamic_patterns: list[str]
    if project_slug is not None:
        project = await ctx.project_store.get_project(project_slug)
        if project is None:
            raise ValueError(f"No project named '{project_slug}'.")
        target = project.directory
        new_slug: str | None = project_slug
        dynamic_patterns = project.enabled_tools
    elif directory_override is not None:
        target = Path(directory_override)
        new_slug = None
        dynamic_patterns = []
    else:
        target = ctx.default_workspace_root.current
        new_slug = None
        dynamic_patterns = []

    ctx.workspace_root.set_current(target)
    await ctx.mcp_manager.sync_directory_following_servers(ctx.workspace_root.current)
    ctx.active_project.current_slug = new_slug
    ctx.leader_view.set_dynamic_patterns(dynamic_patterns)
    await sync_leader_system_prompt(ctx.leader_engine, ctx.base_leader_system_prompt, ctx.project_store, ctx.active_project)


async def use_project(ctx: CLIContext, slug: str) -> ProjectInfo:
    """Raises ValueError if no such project exists. Switches immediately --
    no restart, and deliberately NOT persisted to .env (see
    ActiveProjectState's own docstring: project selection is meant to be a
    frequent, optional, per-session choice, unlike /workspace set's
    standing setting). A thin wrapper around sync_active_directory() that
    also validates up front so it can give a richer 404 message (pointing
    at /project list) than that shared function's own generic one."""
    project = await ctx.project_store.get_project(slug)
    if project is None:
        raise ValueError(f"No project named '{slug}'. Use /project list to see what exists.")
    await sync_active_directory(ctx, project_slug=slug)
    return project


async def exit_project(ctx: CLIContext) -> None:
    """Drops the active project, falling back to whatever
    sync_active_directory() resolves with no project_slug/directory_override
    (ctx.default_workspace_root, i.e. /workspace set's persisted value) --
    see that function's own docstring for why this no longer needs to
    "restore" a remembered snapshot."""
    await sync_active_directory(ctx, project_slug=None)


async def set_project_role(ctx: CLIContext, slug: str, role: str) -> ProjectSummary:
    """Human-direct edit of a project's `role` -- works on ANY project by
    slug, not just the currently active one (unlike the LLM's
    update_project_summary, which only ever acts on whichever project is
    active). If `slug` happens to be the active project, the Leader's
    system prompt is re-synced immediately so the edit takes effect
    starting the very next message, same as an AI-made edit would."""
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    summary = await ctx.project_store.update_summary(slug, role=role)
    if ctx.active_project.current_slug == slug:
        await sync_leader_system_prompt(ctx.leader_engine, ctx.base_leader_system_prompt, ctx.project_store, ctx.active_project)
    return summary


async def set_project_current_state(ctx: CLIContext, slug: str, current_state: str) -> ProjectSummary:
    """Human-direct edit of a project's `current_state` -- same shape as
    set_project_role() above (works on any project by slug, re-syncs the
    Leader's system prompt immediately if it's the active one). Added
    alongside retiring ProjectSummary.outputs (see project_store.py's
    module docstring) so a human can directly correct/update the progress
    narrative, not just the AI."""
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    summary = await ctx.project_store.update_summary(slug, current_state=current_state)
    if ctx.active_project.current_slug == slug:
        await sync_leader_system_prompt(ctx.leader_engine, ctx.base_leader_system_prompt, ctx.project_store, ctx.active_project)
    return summary


async def list_project_memory(ctx: CLIContext, slug: str) -> list[Fact]:
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    return await ctx.project_store.memory_store_for(slug).search_facts("")


async def add_project_memory(ctx: CLIContext, slug: str, content: str) -> Fact:
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    return await ctx.project_store.memory_store_for(slug).add_fact(content)


async def update_project_memory(ctx: CLIContext, slug: str, fact_id: str, content: str) -> Fact:
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    return await ctx.project_store.memory_store_for(slug).update_fact(fact_id, content)


async def delete_project_memory(ctx: CLIContext, slug: str, fact_id: str) -> None:
    if await ctx.project_store.get_project(slug) is None:
        raise ValueError(f"No project named '{slug}'.")
    await ctx.project_store.memory_store_for(slug).delete_fact(fact_id)


@dataclass
class ToolCandidate:
    pattern: str
    label: str
    source: str  # "mcp" | "skill"


def _is_pattern_already_allowed(ctx: CLIContext, pattern: str) -> bool:
    """True if every real tool name currently matching `pattern` in the
    shared registry is already visible to the Leader without any
    project-scoped grant -- i.e. enabling it for a project would add
    nothing. Checked against real registered names, not the raw pattern
    string, because ScopedToolRegistryView.is_allowed() does exact-name
    fnmatch matching against ITS OWN patterns, not pattern-vs-pattern
    comparison. False (offer it as a candidate) if nothing matches yet --
    e.g. an MCP server connected with zero tools, or a not-yet-loaded name."""
    matching = [spec.name for spec in ctx.registry.get_tool_specs() if fnmatch(spec.name, pattern)]
    return bool(matching) and all(ctx.leader_view.is_allowed(name) for name in matching)


def get_available_project_tools(ctx: CLIContext) -> list[ToolCandidate]:
    """Skill/MCP tools installed but NOT already globally granted to the
    orchestrator -- the pool a project can additionally enable for
    itself. Grain is per-SOURCE (a whole MCP server, or a whole Skill),
    matching the existing grant_access(f"mcp_{name}_*") convention
    (tools/self_extend/propose_mcp_tool.py) rather than a finer per-tool
    one. Purely additive candidates -- native tools and anything already
    in config/agents.json's capabilities never appear here."""
    candidates: list[ToolCandidate] = []
    for server_name in ctx.mcp_manager.connected_servers:
        pattern = f"mcp_{server_name}_*"
        if not _is_pattern_already_allowed(ctx, pattern):
            candidates.append(ToolCandidate(pattern=pattern, label=server_name, source="mcp"))
    for skill_name in ctx.skill_loader.registered_skill_names:
        if not _is_pattern_already_allowed(ctx, skill_name):
            candidates.append(ToolCandidate(pattern=skill_name, label=skill_name, source="skill"))
    return candidates


async def set_project_tools(ctx: CLIContext, slug: str, patterns: list[str]) -> ProjectInfo:
    """Human-direct: replaces the given project's enabled_tools wholesale
    (not merged -- same shape as rename_project). If `slug` is the
    currently active project, also re-syncs the Leader's ScopedToolRegistryView
    dynamic patterns immediately, same "active one takes effect now"
    pattern as set_project_role."""
    project = await ctx.project_store.set_enabled_tools(slug, patterns)
    if ctx.active_project.current_slug == slug:
        ctx.leader_view.set_dynamic_patterns(project.enabled_tools)
    return project


# --- schedules (N8 proactivity, tools/scheduler/) ---------------------------
# Human-direct management of ScheduledTask entries -- the /schedule command
# family's backing logic (cli/commands.py). Centralized, not nested under
# /project: a schedule is optionally associated with a project (validated
# here when a slug is given), but managing it doesn't require being
# "inside" that project. Direct human action, no confirmation gate -- same
# precedent as /project tools' enable/disable.


async def _validate_project_slug(ctx: CLIContext, project_slug: str | None) -> None:
    if project_slug is not None and await ctx.project_store.get_project(project_slug) is None:
        raise ValueError(f"No project named '{project_slug}'.")


async def list_schedules(ctx: CLIContext) -> list[ScheduledTask]:
    return await ctx.schedule_store.list_schedules()


async def create_once_schedule(ctx: CLIContext, run_at: str, task: str, project_slug: str | None = None) -> ScheduledTask:
    await _validate_project_slug(ctx, project_slug)
    return await ctx.schedule_store.create_schedule(
        task=task, trigger_type="once", run_at=run_at, project_slug=project_slug
    )


async def create_cron_schedule(
    ctx: CLIContext, cron_expression: str, task: str, project_slug: str | None = None
) -> ScheduledTask:
    await _validate_project_slug(ctx, project_slug)
    return await ctx.schedule_store.create_schedule(
        task=task, trigger_type="recurring", cron_expression=cron_expression, project_slug=project_slug
    )


async def edit_schedule(
    ctx: CLIContext,
    schedule_id: str,
    task: str | None = None,
    run_at: str | None = None,
    cron_expression: str | None = None,
    project_slug: Any = UNSET,
) -> ScheduledTask:
    if project_slug is not UNSET:
        await _validate_project_slug(ctx, project_slug)
    return await ctx.schedule_store.update_schedule(
        schedule_id, task=task, run_at=run_at, cron_expression=cron_expression, project_slug=project_slug
    )


async def set_schedule_enabled(ctx: CLIContext, schedule_id: str, enabled: bool) -> ScheduledTask:
    return await ctx.schedule_store.set_enabled(schedule_id, enabled)


async def delete_schedule(ctx: CLIContext, schedule_id: str) -> None:
    await ctx.schedule_store.delete_schedule(schedule_id)


# --- devices (N14 -- Auralis / remote access) -------------------------------
# Registering a device is a local, trusted action: whoever can type at this
# Windows machine's own REPL already has full control over it, so there's
# no separate approval step the way propose_* self-extension tools have --
# same precedent as /config set-key.


async def list_devices(ctx: CLIContext) -> list[DeviceInfo]:
    return await ctx.device_store.list_devices()


async def create_device(ctx: CLIContext, name: str) -> tuple[DeviceInfo, str]:
    """Returns (DeviceInfo, raw_token) -- see DeviceStore.create_device()'s
    own docstring for why the raw token is never retrievable again after
    this call returns."""
    return await ctx.device_store.create_device(name)


async def revoke_device(ctx: CLIContext, device_id: str) -> None:
    """Raises ValueError for an unknown device_id."""
    await ctx.device_store.revoke_device(device_id)


# --- chat history reconstruction (shared by gui/server.py and telegram_bot.py) ---


def history_from_events(events: list[dict[str, Any]], leader_name: str) -> list[ConversationTurn]:
    """Rebuilds a text-only `history` from a session's persisted event
    stream, keeping only TOP-LEVEL turns (agent_name == the Leader's own
    name) -- a delegated Worker's own user_input/final_answer events carry
    a different agent_name and belong to that sub-task, not the resumed
    conversation's own memory. Deliberately a simplified stand-in for the
    engine's real internal `history` (which also carries raw provider
    tool-call turns, per AsyncReActEngine's own docstring): resuming a
    session gives the model back the conversational gist (what was asked,
    what was answered), not an exact tool-call replay. This is the same
    tradeoff essentially every consumer chat product makes, and keeps a
    saved session provider-agnostic (switching LLM provider between
    sessions never corrupts one)."""
    history: list[ConversationTurn] = []
    for event in events:
        if event.get("agent_name") != leader_name:
            continue
        if event["event_type"] == "user_input":
            history.append(ConversationTurn(role="user", text=event["payload"]["text"]))
        elif event["event_type"] == "final_answer":
            history.append(ConversationTurn(role="assistant", text=event["payload"]["text"]))
    return history
