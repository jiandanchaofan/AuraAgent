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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import set_key

from agents.agent_builder import build_worker, ensure_worker_name_available
from agents.agent_config_writer import add_agent_entry, add_capability, remove_agent_entry
from agents.agent_definition import AgentDefinition
from cli.context import CLIContext
from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from skills.skill_package import StagedSkillInstall, extract_skill_files, finalize_skill_install, stage_skill_install

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
