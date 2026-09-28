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
from pathlib import Path
from typing import Any

from dotenv import set_key

from agents.agent_builder import build_worker, ensure_worker_name_available
from agents.agent_config_writer import add_agent_entry, add_capability, remove_agent_entry
from agents.agent_definition import AgentDefinition
from cli.context import CLIContext
from config.settings import PROJECT_ROOT
from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from skills.skill_package import StagedSkillInstall, extract_skill_files, finalize_skill_install, stage_skill_install
from tools.calendar.google_auth import load_credentials as load_google_credentials
from tools.calendar.google_calendar_provider import GoogleCalendarProvider
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.projects.project_store import ProjectInfo, sync_leader_system_prompt

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
        new_provider = AnthropicProvider(api_key=api_key, model=model_id)
    else:
        new_provider = OpenAIProvider(api_key=api_key, model=model_id, base_url=ctx.settings.openai_base_url)
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


# --- workspace -------------------------------------------------------------


@dataclass
class WorkspaceStatus:
    path: Path


def get_workspace_status(ctx: CLIContext) -> WorkspaceStatus:
    return WorkspaceStatus(path=ctx.workspace_root.current)


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


def _reject_if_windows_system_dir(path: Path) -> None:
    if path in _windows_drive_roots():
        raise ValueError(f"'{path}' is a drive root and cannot be used as the workspace.")
    for root in _windows_blocked_subtree_roots():
        if path == root or root in path.parents:
            raise ValueError(f"'{path}' is a Windows system directory (or inside one: '{root}') and cannot be used as the workspace.")


def _reject_if_touches_project_dir(path: Path) -> None:
    """Blocks both directions: pointing INTO AuraAgent's own project
    directory (its source/config/.env would become reachable through the
    file tools) and pointing at something that CONTAINS it (same result,
    just from the other side)."""
    project_root = PROJECT_ROOT.resolve()
    if path == project_root or project_root in path.parents or path in project_root.parents:
        raise ValueError(f"'{path}' is (or contains, or is contained by) AuraAgent's own project directory ('{project_root}') and cannot be used as the workspace.")


def set_workspace_root(new_path: str, ctx: CLIContext) -> Path:
    """Validates and switches the shared SwappableWorkspaceRoot, then
    persists the new value to .env the same way set_api_key() does, so it
    survives a restart. Raises ValueError with a human-readable reason for
    any rejected path -- a Windows system directory, AuraAgent's own
    project directory, an existing non-directory path, or (real bug this
    caught) a drive letter that doesn't exist at all: Path.mkdir(parents=
    True) can only create directories on an already-mounted volume, so
    pointing at a nonexistent drive (e.g. 'D:\\...' on a machine with no
    D: drive) raises a raw FileNotFoundError -- WinError 3 -- rather than
    the clean, human-readable rejection every other invalid path gets
    here."""
    resolved = Path(new_path).expanduser().resolve()
    _reject_if_windows_system_dir(resolved)
    _reject_if_touches_project_dir(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"'{resolved}' already exists and is not a directory.")

    try:
        ctx.workspace_root.set_current(resolved)  # creates the directory if needed
    except OSError as exc:
        raise ValueError(f"Could not create or access '{resolved}': {exc}") from exc
    set_key(str(ctx.env_file_path), "AURA_WORKSPACE_ROOT", str(resolved))
    return resolved


# --- notes -------------------------------------------------------------


@dataclass
class NotesRootStatus:
    path: Path


def get_notes_root_status(ctx: CLIContext) -> NotesRootStatus:
    return NotesRootStatus(path=ctx.notes_root.current)


def set_notes_root(new_path: str, ctx: CLIContext) -> Path:
    """Same validation/persistence as set_workspace_root() above, applied to
    the independent notes sandbox instead -- e.g. pointing it at a real
    Obsidian vault so search_notes/read_note/create_note/update_note read
    and write real notes instead of the repo-local sandbox default."""
    resolved = Path(new_path).expanduser().resolve()
    _reject_if_windows_system_dir(resolved)
    _reject_if_touches_project_dir(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"'{resolved}' already exists and is not a directory.")

    try:
        ctx.notes_root.set_current(resolved)  # creates the directory if needed
    except OSError as exc:
        raise ValueError(f"Could not create or access '{resolved}': {exc}") from exc
    set_key(str(ctx.env_file_path), "AURA_NOTES_SANDBOX_ROOT", str(resolved))
    return resolved


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


# --- projects ------------------------------------------------------------


@dataclass
class ProjectStatus:
    active_slug: str | None
    projects: list[ProjectInfo]


async def get_project_status(ctx: CLIContext) -> ProjectStatus:
    projects = await ctx.project_store.list_projects()
    return ProjectStatus(active_slug=ctx.active_project.current_slug, projects=projects)


async def create_project(ctx: CLIContext, slug: str, directory: str | None = None) -> ProjectInfo:
    """Raises ValueError for an invalid/duplicate slug, or (when `directory`
    points at an existing path the caller wants to adopt rather than
    auto-create one) the same Windows-system-dir/AuraAgent-own-project-dir
    rejections set_workspace_root() already established -- reused here,
    not duplicated, since a project's directory becomes workspace_root
    too once /project use enters it."""
    if directory:
        resolved = Path(directory).expanduser().resolve()
        _reject_if_windows_system_dir(resolved)
        _reject_if_touches_project_dir(resolved)
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"'{resolved}' already exists and is not a directory.")
    else:
        resolved = (ctx.settings.projects_dir / slug).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return await ctx.project_store.create_project(slug, slug, resolved)


async def use_project(ctx: CLIContext, slug: str) -> ProjectInfo:
    """Raises ValueError if no such project exists. Switches immediately --
    no restart, and deliberately NOT persisted to .env (see
    ActiveProjectState's own docstring: project selection is meant to be a
    frequent, optional, per-session choice, unlike /workspace set's
    standing setting). Repoints ctx.workspace_root exactly like
    /workspace set does (every tool already scoped to it -- tools/files/,
    screenshots, downloads, Skill subprocesses -- follows along), and
    rewrites ctx.leader_engine.system_prompt to include this project's
    summary, in effect starting the very next message."""
    project = await ctx.project_store.get_project(slug)
    if project is None:
        raise ValueError(f"No project named '{slug}'. Use /project list to see what exists.")
    if ctx.active_project.pre_project_workspace is None:
        ctx.active_project.pre_project_workspace = ctx.workspace_root.current
    ctx.workspace_root.set_current(project.directory)
    ctx.active_project.current_slug = slug
    await sync_leader_system_prompt(ctx.leader_engine, ctx.base_leader_system_prompt, ctx.project_store, ctx.active_project)
    return project


async def exit_project(ctx: CLIContext) -> None:
    """Restores workspace_root to whatever it was immediately before the
    FIRST /project use this session (a single snapshot, not a stack --
    switching directly between two projects never touches it), and drops
    the project summary out of the Leader's system prompt."""
    if ctx.active_project.pre_project_workspace is not None:
        ctx.workspace_root.set_current(ctx.active_project.pre_project_workspace)
        ctx.active_project.pre_project_workspace = None
    ctx.active_project.current_slug = None
    await sync_leader_system_prompt(ctx.leader_engine, ctx.base_leader_system_prompt, ctx.project_store, ctx.active_project)
