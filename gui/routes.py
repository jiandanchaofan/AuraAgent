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

import base64
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.agent_definition import AgentDefinitionError
from agents.agent_registry import AgentRegistryError
from cli import service
from cli.context import CLIContext
from skills.skill_package import SkillPackageError, StagedSkillInstall
from skills.skill_schema import SkillManifestError

router = APIRouter(prefix="/api")


def _cli_ctx(request: Request) -> CLIContext:
    return request.app.state.ctx.cli_context


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
