"""gui/routes.py — REST endpoints backing the GUI's Settings/Team/Skills
panels (Epic M3). Every handler here is a thin HTTP adapter over
cli/service.py, the exact same module cli/commands.py's "/" handlers call
— a panel action and its "/" command counterpart always do the same work,
by construction, not by convention. See cli/service.py's module docstring
for the split this relies on.

Read endpoints (GET) return a live snapshot, computed fresh on every call
(no caching) since the underlying state (config/agents.json, the shared
ToolRegistry) can change from either frontend, the self-extension tools,
or a hot-loaded Skill at any time.

Write endpoints skip the extra "review, then a separate approval step"
dance propose_new_agent/propose_new_skill built for LLM-initiated
proposals: a human filling out a form and pressing submit already IS the
approval, exactly like the "/" commands' own final [y/N] prompt is
described as "guarding against a typo, not against LLM overreach" (see
README). The one exception is installing a Skill (/api/skills/stage then
/api/skills/commit as two separate calls) — that mirrors /skills load|
install's own two-phase "show the full code, then ask" shape, because
unlike adding an agent or granting a capability, a human should actually
read an externally-sourced Skill's source before approving it, not just
fill in a name and hit submit.
"""
from __future__ import annotations

import asyncio
import base64
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents.agent_definition import AgentDefinitionError
from agents.agent_registry import AgentRegistryError
from cli import service
from cli.context import CLIContext
from core.exceptions import SandboxPathError
from skills.skill_package import SkillPackageError, StagedSkillInstall
from skills.skill_schema import SkillManifestError
from tools.calendar import google_auth
from tools.projects.project_store import ProjectInfo
from tools.tasks import google_tasks_auth
from tools.scheduler.cron_utils import describe_schedule
from tools.sandbox_path import resolve_within_sandbox
from tools.sessions.session_store import ChatSessionStore, SessionInfo

router = APIRouter(prefix="/api")


def _cli_ctx(request: Request) -> CLIContext:
    return request.app.state.ctx.cli_context


def _session_store(request: Request) -> ChatSessionStore:
    return request.app.state.session_store


# --- /api/config ---------------------------------------------------------


class ConfigUseBody(BaseModel):
    provider: str
    model: str


class ConfigSetKeyBody(BaseModel):
    provider: str
    value: str


@router.get("/config")
def get_config(request: Request):
    status = service.get_config_status(_cli_ctx(request))
    return {"provider": status.provider, "model": status.model, "key_masked": status.key_masked}


@router.post("/config/use")
def post_config_use(body: ConfigUseBody, request: Request):
    try:
        service.switch_provider(body.provider, body.model, _cli_ctx(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_config(request)


@router.post("/config/set-key")
def post_config_set_key(body: ConfigSetKeyBody, request: Request):
    try:
        service.set_api_key(body.provider, body.value, _cli_ctx(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_config(request)


# --- /api/agents -----------------------------------------------------------


class AddAgentBody(BaseModel):
    name: str
    system_prompt: str
    capabilities: list[str]


@router.get("/agents")
def get_agents(request: Request):
    return [
        {"name": a.name, "role": a.role, "capabilities": a.capabilities} for a in service.list_agents(_cli_ctx(request))
    ]


@router.post("/agents")
async def post_agents(body: AddAgentBody, request: Request):
    ctx = _cli_ctx(request)
    try:
        agent_def = service.validate_new_agent(body.name, body.system_prompt, body.capabilities, ctx)
    except AgentDefinitionError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent definition: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await service.commit_new_agent(agent_def, ctx)
    return {"name": agent_def.name, "role": "worker", "capabilities": agent_def.capabilities}


@router.delete("/agents/{name}")
async def delete_agent(name: str, request: Request):
    try:
        await service.remove_agent(name, _cli_ctx(request))
    except AgentRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": name}


# --- /api/skills -----------------------------------------------------------


class StageSkillBody(BaseModel):
    source: str  # "url" | "upload"
    url: str | None = None
    filename: str | None = None
    content_base64: str | None = None


class CommitSkillBody(BaseModel):
    staging_id: str


def _staged_skills(request: Request) -> dict[str, StagedSkillInstall]:
    return request.app.state.staged_skills


@router.get("/skills")
def get_skills(request: Request):
    return [{"name": s.name, "description": s.description} for s in service.list_skills(_cli_ctx(request))]


@router.post("/skills/stage")
async def post_skills_stage(body: StageSkillBody, request: Request):
    ctx = _cli_ctx(request)

    if body.source == "url":
        if not body.url:
            raise HTTPException(status_code=400, detail="'url' is required when source='url'.")
    elif body.source == "upload":
        if not body.content_base64:
            raise HTTPException(status_code=400, detail="'content_base64' is required when source='upload'.")
        try:
            data = base64.b64decode(body.content_base64)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail="'source' must be 'url' or 'upload'.")

    try:
        if body.source == "url":
            staged = await service.stage_skill_from_url(body.url, ctx)
        else:
            staged = service.stage_skill_from_bytes(data, ctx)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (SkillPackageError, SkillManifestError) as exc:
        raise HTTPException(status_code=422, detail=f"Rejected: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - a download/HTTP failure, report to the human
        raise HTTPException(status_code=502, detail=f"Download failed: {exc}") from exc

    staging_id = uuid4().hex
    _staged_skills(request)[staging_id] = staged
    return {
        "staging_id": staging_id,
        "name": staged.manifest.name,
        "skill_md_text": staged.skill_md_text,
        "run_py_text": staged.run_py_text,
        "warnings": staged.warnings,
    }


@router.post("/skills/commit")
async def post_skills_commit(body: CommitSkillBody, request: Request):
    pending = _staged_skills(request)
    staged = pending.pop(body.staging_id, None)
    if staged is None:
        raise HTTPException(status_code=404, detail="Unknown or already-used staging_id.")

    try:
        registered_name = await service.commit_skill(staged, _cli_ctx(request))
    except Exception as exc:  # noqa: BLE001 - a registration failure, surfaced plainly
        raise HTTPException(status_code=500, detail=f"Failed to install: {exc}") from exc
    return {"name": registered_name}


# --- /api/workspace ----------------------------------------------------------


class PathBody(BaseModel):
    path: str


@router.get("/workspace")
def get_workspace(request: Request):
    status = service.get_workspace_status(_cli_ctx(request))
    return {"path": str(status.path), "default": str(status.default)}


@router.post("/workspace")
async def post_workspace(body: PathBody, request: Request):
    try:
        update = await service.set_workspace_root(body.path, _cli_ctx(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(update.default), "deferred": update.deferred}


# --- /api/notes ----------------------------------------------------------


@router.get("/notes")
def get_notes(request: Request):
    status = service.get_notes_root_status(_cli_ctx(request))
    return {"path": str(status.path), "quick_notes_subdir": status.quick_notes_subdir}


@router.post("/notes")
def post_notes(body: PathBody, request: Request):
    try:
        resolved = service.set_notes_root(body.path, _cli_ctx(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(resolved)}


class QuickNotesSubdirBody(BaseModel):
    subdir: str


@router.post("/notes/quick-notes-dir")
def post_quick_notes_dir(body: QuickNotesSubdirBody, request: Request):
    """N14 (Auralis / remote access) -- which subdirectory under the notes
    root quick_note_sync (gui/server.py) appends into, e.g. 'Daily Notes'
    for Obsidian's Thino plugin convention."""
    try:
        resolved = service.set_quick_notes_subdir(body.subdir, _cli_ctx(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"quick_notes_subdir": resolved}


# --- /api/calendar ---------------------------------------------------------


@router.get("/calendar")
def get_calendar(request: Request):
    status = service.get_calendar_status(_cli_ctx(request))
    return {"backend": status.backend}


@router.post("/calendar/connect")
async def post_calendar_connect(request: Request):
    ctx = _cli_ctx(request)
    try:
        service.check_google_client_secret(ctx)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        # Blocking -- opens a browser and waits for the user to approve the
        # consent screen. cli/commands.py's _cmd_calendar does this inline
        # because the REPL is single-threaded anyway; here it would freeze
        # every other WebSocket/HTTP request this process is serving, so
        # it's pushed off the event loop instead. The request just takes a
        # while to return, exactly like the CLI blocks until the browser
        # flow completes.
        await asyncio.to_thread(
            google_auth.run_oauth_flow, ctx.settings.google_client_secret_file, ctx.settings.google_token_file
        )
    except Exception as exc:  # noqa: BLE001 - report cleanly, mirrors cli/commands.py's own handling
        raise HTTPException(status_code=502, detail=f"Google authorization failed: {exc}") from exc
    try:
        service.finish_google_calendar_connect(ctx)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return get_calendar(request)


@router.post("/calendar/disconnect")
def post_calendar_disconnect(request: Request):
    service.disconnect_google_calendar(_cli_ctx(request))
    return get_calendar(request)


# --- /api/tasks --------------------------------------------------------------
# Connection status only -- no task CRUD here. AuraAgent writes tasks into
# the user's real Google Tasks account (a dedicated "AuraAgent" list); the
# user views/manages them in the real Google Tasks app, not a second GUI
# here (explicit product decision -- don't duplicate that UI).


@router.get("/tasks/backend")
def get_tasks_backend(request: Request):
    status = service.get_task_backend_status(_cli_ctx(request))
    return {"backend": status.backend}


@router.post("/tasks/backend/connect")
async def post_tasks_connect(request: Request):
    ctx = _cli_ctx(request)
    try:
        service.check_google_tasks_client_secret(ctx)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await asyncio.to_thread(
            google_tasks_auth.run_oauth_flow, ctx.settings.google_client_secret_file, ctx.settings.google_tasks_token_file
        )
    except Exception as exc:  # noqa: BLE001 - report cleanly, mirrors cli/commands.py's own handling
        raise HTTPException(status_code=502, detail=f"Google authorization failed: {exc}") from exc
    try:
        await service.finish_google_tasks_connect(ctx)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return get_tasks_backend(request)


@router.post("/tasks/backend/disconnect")
def post_tasks_disconnect(request: Request):
    service.disconnect_google_tasks(_cli_ctx(request))
    return get_tasks_backend(request)


# --- /api/projects -----------------------------------------------------------


class ProjectCreateBody(BaseModel):
    slug: str
    directory: str | None = None


def _project_json(project: ProjectInfo) -> dict:
    return {"slug": project.slug, "name": project.name, "directory": str(project.directory), "created_at": project.created_at}


@router.get("/projects")
async def get_projects(request: Request):
    status = await service.get_project_status(_cli_ctx(request))
    return {"active_slug": status.active_slug, "projects": [_project_json(p) for p in status.projects]}


@router.post("/projects")
async def post_projects(body: ProjectCreateBody, request: Request):
    try:
        project = await service.create_project(_cli_ctx(request), body.slug, body.directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_json(project)


@router.post("/projects/{slug}/use")
async def post_project_use(slug: str, request: Request):
    try:
        project = await service.use_project(_cli_ctx(request), slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _project_json(project)


@router.post("/projects/none")
async def post_project_none(request: Request):
    await service.exit_project(_cli_ctx(request))
    return {"active_slug": None}


class RenameProjectBody(BaseModel):
    name: str


@router.patch("/projects/{slug}")
async def patch_project(slug: str, body: RenameProjectBody, request: Request):
    try:
        project = await service.rename_project(_cli_ctx(request), slug, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_json(project)


# --- /api/projects/{slug}/files ----------------------------------------------
# Read-only directory listing for the Project detail view's 目录 tab, scoped
# to THAT project's own directory regardless of which workspace is currently
# active (a project need not be the active one to browse its files) --
# resolve_within_sandbox() is the same traversal guard tools/files/
# file_tool.py's list_directory uses, just pointed at project.directory
# instead of the ambient workspace_root.


def _file_entry_json(path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "is_dir": path.is_dir(),
        "size": None if path.is_dir() else stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


@router.get("/projects/{slug}/files")
async def get_project_files(slug: str, request: Request, path: str = ""):
    project = await request.app.state.ctx.project_store.get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"No project named '{slug}'.")
    try:
        target = resolve_within_sandbox(project.directory, path)
    except SandboxPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: '{path}'")
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    return [_file_entry_json(p) for p in entries]


async def _project_or_404(request: Request, slug: str) -> ProjectInfo:
    project = await request.app.state.ctx.project_store.get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"No project named '{slug}'.")
    return project


def _resolve_or_400(project: ProjectInfo, path: str):
    try:
        return resolve_within_sandbox(project.directory, path)
    except SandboxPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- /api/projects/{slug}/files: write operations ---------------------------
# Human-direct file management for the 目录 tab (upload/新建文件夹/重命名/删除/
# 下载) -- a direct GUI action, same "a human filling out a form and
# pressing submit already IS the approval" reasoning as the module
# docstring already states for /api/agents etc., so none of these go
# through ConfirmationChannel (unlike the LLM-facing delete_file, which
# always does). Every path is still guarded by the same
# resolve_within_sandbox() the read-only listing above uses.


@router.post("/projects/{slug}/files/upload")
async def upload_project_file(slug: str, request: Request, path: str = Form(""), file: UploadFile = File(...)):
    project = await _project_or_404(request, slug)
    target_dir = _resolve_or_400(project, path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: '{path}'")
    destination = _resolve_or_400(project, f"{path}/{file.filename}" if path else file.filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    contents = await file.read()
    destination.write_bytes(contents)
    return _file_entry_json(destination)


class MkdirProjectBody(BaseModel):
    path: str


@router.post("/projects/{slug}/files/mkdir")
async def mkdir_project_file(slug: str, body: MkdirProjectBody, request: Request):
    project = await _project_or_404(request, slug)
    target = _resolve_or_400(project, body.path)
    if target.exists() and not target.is_dir():
        raise HTTPException(status_code=409, detail=f"'{body.path}' already exists and is not a directory.")
    target.mkdir(parents=True, exist_ok=True)
    return _file_entry_json(target)


class RenameProjectFileBody(BaseModel):
    path: str
    new_name: str


@router.patch("/projects/{slug}/files")
async def rename_project_file(slug: str, body: RenameProjectFileBody, request: Request):
    project = await _project_or_404(request, slug)
    source = _resolve_or_400(project, body.path)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"'{body.path}' not found.")
    new_name = body.new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        raise HTTPException(status_code=400, detail=f"Invalid new name '{body.new_name}'.")
    destination = source.parent / new_name
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"'{new_name}' already exists.")
    source.rename(destination)
    return _file_entry_json(destination)


@router.delete("/projects/{slug}/files")
async def delete_project_file(slug: str, request: Request, path: str):
    project = await _project_or_404(request, slug)
    target = _resolve_or_400(project, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"'{path}' not found.")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"deleted": path}


@router.get("/projects/{slug}/files/download")
async def download_project_file(slug: str, request: Request, path: str):
    project = await _project_or_404(request, slug)
    target = _resolve_or_400(project, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"'{path}' not found.")
    return FileResponse(target, filename=target.name)


# --- /api/projects/{slug}/summary -------------------------------------------
# Backs the Project detail view's Context tab (Role/current-state/
# open-questions). This is the same ProjectSummary the Leader already
# reads automatically (spliced into its system prompt while the project
# is active, tools/projects/project_tool.py).


@router.get("/projects/{slug}/summary")
async def get_project_summary(slug: str, request: Request):
    project_store = request.app.state.ctx.project_store
    project = await project_store.get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"No project named '{slug}'.")
    summary = await project_store.get_summary(slug)
    return {
        "role": summary.role,
        "current_state": summary.current_state,
        "open_questions": summary.open_questions,
    }


# --- /api/projects/{slug}/role, /api/projects/{slug}/state ------------------
# The Context tab's Role and Current-state editors -- human-direct edits,
# work on ANY project by slug (not just the currently active one), same
# as PATCH /api/projects/{slug} (rename). If `slug` happens to be the
# active project, cli/service.py's set_project_role()/
# set_project_current_state() re-sync the Leader's system prompt
# immediately, no restart needed. (There used to be an `outputs` field/
# endpoint too -- retired, see tools/projects/project_store.py's module
# docstring for why: it was pure duplication of what the project's own
# directory already answers accurately on demand.)


class SetProjectRoleBody(BaseModel):
    role: str


@router.patch("/projects/{slug}/role")
async def patch_project_role(slug: str, body: SetProjectRoleBody, request: Request):
    try:
        summary = await service.set_project_role(_cli_ctx(request), slug, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"role": summary.role}


class SetProjectStateBody(BaseModel):
    current_state: str


@router.patch("/projects/{slug}/state")
async def patch_project_state(slug: str, body: SetProjectStateBody, request: Request):
    try:
        summary = await service.set_project_current_state(_cli_ctx(request), slug, body.current_state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"current_state": summary.current_state}


# --- /api/projects/{slug}/memory --------------------------------------------
# The Context tab's Memory list -- a per-project instance of the same
# MemoryStore the global remember_fact/recall_facts tools use (see
# tools/projects/project_store.py::memory_store_for). Works on ANY
# project by slug, independent of which one (if any) is currently active
# -- unlike remember_project_fact/recall_project_facts, the LLM-facing
# tools, which only ever act on the active one.


def _fact_json(fact) -> dict:
    return {"id": fact.id, "content": fact.content, "created_at": fact.created_at}


@router.get("/projects/{slug}/memory")
async def get_project_memory(slug: str, request: Request):
    try:
        facts = await service.list_project_memory(_cli_ctx(request), slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_fact_json(f) for f in facts]


class AddProjectMemoryBody(BaseModel):
    content: str


@router.post("/projects/{slug}/memory")
async def post_project_memory(slug: str, body: AddProjectMemoryBody, request: Request):
    try:
        fact = await service.add_project_memory(_cli_ctx(request), slug, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fact_json(fact)


class UpdateProjectMemoryBody(BaseModel):
    content: str


@router.patch("/projects/{slug}/memory/{fact_id}")
async def patch_project_memory(slug: str, fact_id: str, body: UpdateProjectMemoryBody, request: Request):
    try:
        fact = await service.update_project_memory(_cli_ctx(request), slug, fact_id, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fact_json(fact)


@router.delete("/projects/{slug}/memory/{fact_id}")
async def delete_project_memory(slug: str, fact_id: str, request: Request):
    try:
        await service.delete_project_memory(_cli_ctx(request), slug, fact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": fact_id}


# --- /api/projects/{slug}/tools ---------------------------------------------
# The Tools tab: which already-installed Skill/MCP tools this project has
# additionally enabled for itself (on top of the orchestrator's always-on
# config/agents.json capabilities), plus which other installed-but-not-
# globally-granted ones are available to enable. Purely additive, purely
# human-managed -- direct action, no ConfirmationChannel round trip,
# same precedent as /api/agents add/remove.


@router.get("/projects/{slug}/tools")
async def get_project_tools(slug: str, request: Request):
    ctx = _cli_ctx(request)
    project = await ctx.project_store.get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"No project named '{slug}'.")
    candidates = service.get_available_project_tools(ctx)
    return {
        "enabled": project.enabled_tools,
        "available": [{"pattern": c.pattern, "label": c.label, "source": c.source} for c in candidates],
    }


class SetProjectToolsBody(BaseModel):
    enabled: list[str]


@router.put("/projects/{slug}/tools")
async def put_project_tools(slug: str, body: SetProjectToolsBody, request: Request):
    try:
        project = await service.set_project_tools(_cli_ctx(request), slug, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"enabled": project.enabled_tools}


# --- /api/mcp --------------------------------------------------------------
# Read-only for now -- adding a new MCP server still only happens via
# propose_mcp_server (LLM-proposed, human-approved) or hand-editing
# config/mcp_servers.json + a restart; no direct-install form the way
# /api/skills/stage+commit gives Skills.


@router.get("/mcp")
def get_mcp_servers(request: Request):
    servers = service.list_mcp_servers(_cli_ctx(request))
    return [{"name": s.name, "tool_count": s.tool_count} for s in servers]


# --- /api/schedules ----------------------------------------------------------
# N8 proactivity (tools/scheduler/): tasks that run without a human
# re-triggering them, once or on a schedule. Deliberately top-level, not
# nested under /api/projects/{slug}/... -- backs the Schedules panel's
# centralized view across every project (plus unaffiliated tasks), the
# same "one place to manage all of them" requirement that shaped the CLI's
# top-level /schedule command too. Direct human action, no confirmation
# gate -- same precedent as PUT .../tools.


def _schedule_json(s) -> dict:
    return {
        "id": s.id,
        "task": s.task,
        "trigger_type": s.trigger_type,
        "project_slug": s.project_slug,
        "run_at": s.run_at,
        "cron_expression": s.cron_expression,
        "description": describe_schedule(s.trigger_type, s.run_at, s.cron_expression),
        "enabled": s.enabled,
        "created_at": s.created_at,
        "last_run_at": s.last_run_at,
        "last_result_summary": s.last_result_summary,
        "next_run_at": s.next_run_at,
    }


@router.get("/schedules")
async def get_schedules(request: Request, project: str | None = None):
    ctx = _cli_ctx(request)
    schedules = await ctx.schedule_store.list_schedules(project_slug=project)
    return [_schedule_json(s) for s in schedules]


class CreateScheduleBody(BaseModel):
    task: str
    trigger_type: str
    run_at: str | None = None
    cron_expression: str | None = None
    project_slug: str | None = None


@router.post("/schedules")
async def post_schedule(body: CreateScheduleBody, request: Request):
    ctx = _cli_ctx(request)
    try:
        if body.trigger_type == "once":
            schedule = await service.create_once_schedule(ctx, body.run_at, body.task, body.project_slug)
        elif body.trigger_type == "recurring":
            schedule = await service.create_cron_schedule(ctx, body.cron_expression, body.task, body.project_slug)
        else:
            raise HTTPException(status_code=400, detail="trigger_type must be 'once' or 'recurring'.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _schedule_json(schedule)


class UpdateScheduleBody(BaseModel):
    task: str | None = None
    run_at: str | None = None
    cron_expression: str | None = None
    #: Three-way: omitted (don't touch), null (unaffiliate), or a slug --
    #: pydantic can't distinguish "omitted" from "explicit null" on a
    #: plain Optional field, so this uses model_fields_set below instead.
    project_slug: str | None = None
    enabled: bool | None = None


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(schedule_id: str, body: UpdateScheduleBody, request: Request):
    ctx = _cli_ctx(request)
    try:
        if body.enabled is not None:
            await service.set_schedule_enabled(ctx, schedule_id, body.enabled)
        project_kwarg: dict = {}
        if "project_slug" in body.model_fields_set:
            project_kwarg["project_slug"] = body.project_slug
        schedule = await service.edit_schedule(
            ctx, schedule_id, task=body.task, run_at=body.run_at, cron_expression=body.cron_expression, **project_kwarg
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _schedule_json(schedule)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request):
    try:
        await service.delete_schedule(_cli_ctx(request), schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": schedule_id}


# --- /api/sessions -------------------------------------------------------
# Backs the sidebar's session list (gui/server.py persists/resumes the
# actual conversations over the WebSocket -- these endpoints are the
# read/rename/delete half a REST panel needs, same split as everywhere
# else in this file).


class RenameSessionBody(BaseModel):
    title: str


class SetSessionProjectBody(BaseModel):
    slug: str | None = None


class SetSessionDirectoryBody(BaseModel):
    directory: str | None = None


def _session_json(info: SessionInfo) -> dict:
    return {
        "id": info.id,
        "title": info.title,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
        "pinned": info.pinned,
        "project_slug": info.project_slug,
        "directory": info.directory,
    }


@router.get("/sessions")
async def get_sessions(request: Request, project: str | None = None, unaffiliated: bool = False):
    """`project` filters to one Project's chats -- the Project detail
    view's Chat tab passes its slug. `unaffiliated=true` filters to chats
    with no Project tag at all -- the sidebar's own list uses this, since
    a Project-tagged chat is only ever shown inside that Project's own
    Chat tab, not duplicated into the general list too. Neither given,
    every chat is returned regardless of its own tag."""
    sessions = await _session_store(request).list_sessions(project_slug=project, unaffiliated=unaffiliated)
    return [_session_json(s) for s in sessions]


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, body: RenameSessionBody, request: Request):
    try:
        updated = await _session_store(request).rename_session(session_id, body.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_json(updated)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    if request.app.state.session_sink.active_session_id == session_id:
        raise HTTPException(status_code=400, detail="Cannot delete the active chat -- switch to another one first.")
    try:
        await _session_store(request).delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": session_id}


@router.post("/sessions/{session_id}/pin")
async def pin_session(session_id: str, request: Request):
    try:
        updated = await _session_store(request).set_pinned(session_id, True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _session_json(updated)


@router.post("/sessions/{session_id}/unpin")
async def unpin_session(session_id: str, request: Request):
    try:
        updated = await _session_store(request).set_pinned(session_id, False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _session_json(updated)


@router.post("/sessions/{session_id}/project")
async def set_session_project(session_id: str, body: SetSessionProjectBody, request: Request):
    """Manual tag/retag from the sidebar's three-dot menu. Only persists
    the tag -- does NOT eagerly re-sync the Leader's live workspace_root/
    active_project here (N14: a REST request has no connection of its own
    to know whether this session happens to be "the" one live on some
    WebSocket right now, and `session_sink.active_session_id` is a
    process-wide pointer that could belong to an entirely different
    connection -- e.g. an Auralis phone connection, mid-turn; guessing
    wrong would leak this change into someone else's in-flight turn).
    gui/server.py's _run_orchestrator() already re-resolves this
    unconditionally, fresh, from the session's own stored
    project_slug/directory at the start of every turn -- so the next
    message sent on whichever connection has this chat open always picks
    the new tag up correctly; it just won't be reflected in e.g. a
    GET /api/workspace status call made before that next message."""
    ctx = request.app.state.ctx
    if body.slug is not None:
        project = await ctx.project_store.get_project(body.slug)
        if project is None:
            raise HTTPException(status_code=404, detail=f"No project named '{body.slug}'.")
    try:
        updated = await _session_store(request).set_project(session_id, body.slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _session_json(updated)


@router.post("/sessions/{session_id}/directory")
async def set_session_directory(session_id: str, body: SetSessionDirectoryBody, request: Request):
    """A Chat's own working directory -- only actually used while this
    chat has no Project tag; a Project's own directory always outranks it
    (see SessionInfo.directory's own docstring, and
    cli/service.py::sync_active_directory()). Validation reuses the exact
    same rules /workspace set applies (no Windows system directory, and
    it can't be/contain/be-contained-by AuraAgent's own project
    directory) -- an arbitrary real directory outside the sandbox is
    explicitly allowed, same as /workspace set. Only persists the
    directory -- does not eagerly re-sync the live workspace_root here,
    same reasoning as set_session_project above."""
    directory_str: str | None
    if body.directory is not None:
        resolved = Path(body.directory).expanduser().resolve()
        try:
            service.reject_if_windows_system_dir(resolved)
            service.reject_if_touches_project_dir(resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if resolved.exists() and not resolved.is_dir():
            raise HTTPException(status_code=400, detail=f"'{resolved}' already exists and is not a directory.")
        # Created immediately, same as /workspace set does -- not just
        # lazily whenever this chat next happens to become active (that
        # would still work, since SwappableWorkspaceRoot.set_current()
        # also creates it, but a human setting this expects the folder to
        # exist right away, not only once they actually switch to it).
        resolved.mkdir(parents=True, exist_ok=True)
        directory_str = str(resolved)
    else:
        directory_str = None

    try:
        updated = await _session_store(request).set_directory(session_id, directory_str)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _session_json(updated)
