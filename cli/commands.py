"""cli/commands.py — the human-direct "/" command layer.

A line typed at the REPL prompt that starts with "/" never becomes a user
turn sent to the orchestrator: main.py's REPL loop checks is_command()
first and, if true, hands the line to dispatch_command() here instead.
This is a deliberate SECOND channel alongside the LLM-proposal
self-extension tools in tools/self_extend/ (propose_new_skill/
propose_new_agent/propose_mcp_server) — same underlying capabilities
(add/remove a team member, install a Skill), but triggered directly by a
human instead of proposed by the LLM and reviewed by a human. Commands
here therefore skip the "structural pre-check -> human review" step those
tools have (the human typing the command already IS the reviewer), but
still reuse the exact same construction/persistence helpers
(agents/agent_builder.py, agents/agent_config_writer.py,
mcp_integration/mcp_config_writer.py) so both channels stay consistent.

Nothing here ever touches core/react_engine.py, the LLM, or
logs/session-*.jsonl — this is why /config set-key is safe: an API key
entered here is written straight to .env and an in-memory dict, never
passed through anything that logs or shows content to the LLM.
"""
from __future__ import annotations

import getpass
from pathlib import Path
from typing import Any

from dotenv import set_key

from agents.agent_builder import build_worker, ensure_worker_name_available
from agents.agent_config_writer import add_agent_entry, add_capability, remove_agent_entry
from agents.agent_definition import AgentDefinition, AgentDefinitionError
from agents.agent_registry import AgentRegistryError
from cli.context import CLIContext
from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from skills.skill_package import SkillPackageError, extract_skill_files, finalize_skill_install, stage_skill_install

_ENV_KEY_BY_PROVIDER = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

_HELP_TEXT = """\
Available commands:
  /help                            Show this help
  /config                          Show the current provider/model (key masked)
  /config use <provider> <model>   Switch every agent to a provider ('anthropic'|'openai') + model
  /config set-key <provider>       Set/update an API key (hidden input, saved to .env)
  /agents                          List the current team
  /agents add                      Add a new worker agent (interactive)
  /agents remove <name>            Remove a worker agent
  /skills                          List installed skills
  /skills load <url>               Download and install a Skill package (.zip) from a URL
  /skills install <path>           Install a Skill package (.zip) from a local file
  exit                             Quit AuraAgent
"""


def is_command(line: str) -> bool:
    return line.startswith("/")


async def dispatch_command(line: str, ctx: CLIContext) -> None:
    stripped = line.strip()
    if not stripped:
        return
    # partition, not str.split() into a full token list — a local file path
    # (/skills install <path>) can itself contain spaces on Windows, and
    # this way the trailing argument is taken verbatim as everything after
    # the subcommand, with no quoting syntax for the user to get wrong.
    command, _, rest = stripped.partition(" ")
    rest = rest.strip()

    if command == "/help":
        print(_HELP_TEXT)
    elif command == "/config":
        await _cmd_config(rest.split(), ctx)
    elif command == "/agents":
        await _cmd_agents(rest, ctx)
    elif command == "/skills":
        await _cmd_skills(rest, ctx)
    else:
        print(f"Unknown command '{command}'. Type /help for a list of commands.")


# --- /config ---------------------------------------------------------------


async def _cmd_config(args: list[str], ctx: CLIContext) -> None:
    if not args:
        _config_show(ctx)
    elif args[0] == "use" and len(args) >= 3:
        _config_use(args[1], args[2], ctx)
    elif args[0] == "set-key" and len(args) >= 2:
        _config_set_key(args[1], ctx)
    else:
        print("Usage: /config | /config use <anthropic|openai> <model_id> | /config set-key <anthropic|openai>")


def _mask_key(key: str) -> str:
    if len(key) <= 4:
        return "(not set)" if not key else "***"
    return f"{key[:2]}...{key[-2:]}"


def _config_show(ctx: CLIContext) -> None:
    name = ctx.provider.provider_name
    model = ctx.provider.model_name
    key = ctx.known_api_keys.get(name, "")
    print(f"provider={name} model={model} key={_mask_key(key)}")


def _config_use(provider_name: str, model_id: str, ctx: CLIContext) -> None:
    if provider_name not in _ENV_KEY_BY_PROVIDER:
        print(f"Unknown provider '{provider_name}' — expected 'anthropic' or 'openai'.")
        return
    api_key = ctx.known_api_keys.get(provider_name, "")
    if not api_key:
        print(f"No API key known for '{provider_name}' yet — run '/config set-key {provider_name}' first.")
        return

    new_provider: Any
    if provider_name == "anthropic":
        new_provider = AnthropicProvider(api_key=api_key, model=model_id)
    else:
        new_provider = OpenAIProvider(api_key=api_key, model=model_id, base_url=ctx.settings.openai_base_url)
    ctx.provider.set_current(new_provider, provider_name)
    print(f"Switched to provider={provider_name} model={model_id}.")


def _config_set_key(provider_name: str, ctx: CLIContext) -> None:
    if provider_name not in _ENV_KEY_BY_PROVIDER:
        print(f"Unknown provider '{provider_name}' — expected 'anthropic' or 'openai'.")
        return

    value = getpass.getpass(f"Enter API key for '{provider_name}' (input hidden): ").strip()
    if not value:
        print("Empty key — nothing changed.")
        return

    ctx.known_api_keys[provider_name] = value
    set_key(str(ctx.env_file_path), _ENV_KEY_BY_PROVIDER[provider_name], value)
    print(f"Saved. Run '/config use {provider_name} <model_id>' to switch to it now.")


# --- /agents -----------------------------------------------------------------


async def _cmd_agents(rest: str, ctx: CLIContext) -> None:
    if not rest:
        _agents_list(ctx)
        return
    sub, _, arg = rest.partition(" ")
    arg = arg.strip()
    if sub == "add":
        await _agents_add(ctx)
    elif sub == "remove" and arg:
        await _agents_remove(arg, ctx)
    else:
        print("Usage: /agents | /agents add | /agents remove <name>")


def _agents_list(ctx: CLIContext) -> None:
    leader = ctx.agent_registry.leader
    print(f"- {leader.name} (leader): {', '.join(leader.capabilities)}")
    for worker in ctx.agent_registry.workers:
        print(f"- {worker.name} (worker): {', '.join(worker.capabilities)}")


async def _agents_add(ctx: CLIContext) -> None:
    name = input("New agent name: ").strip()
    system_prompt = input("System prompt: ").strip()
    caps_raw = input("Capabilities (comma-separated tool name patterns, e.g. calculate,*task*): ").strip()
    capabilities = [c.strip() for c in caps_raw.split(",") if c.strip()]

    try:
        new_agent = AgentDefinition(name=name, role="worker", system_prompt=system_prompt, capabilities=capabilities)
    except AgentDefinitionError as exc:
        print(f"Invalid agent definition: {exc}")
        return
    try:
        ensure_worker_name_available(name, ctx.agent_registry, ctx.registry)
    except ValueError as exc:
        print(str(exc))
        return

    print(f"\nAbout to add worker '{name}':")
    print(f"  system_prompt: {system_prompt}")
    print(f"  capabilities: {capabilities}")
    if input("Proceed? [y/N]: ").strip().lower() != "y":
        print("Cancelled.")
        return

    delegate_tool = build_worker(new_agent, ctx.registry, ctx.provider, ctx.logger, ctx.max_turns)
    ctx.leader_view.add_extra_tool(delegate_tool)
    ctx.agent_registry.add_worker(new_agent)
    await add_agent_entry(
        ctx.settings.agents_config_path,
        {"name": name, "role": "worker", "system_prompt": system_prompt, "capabilities": capabilities},
        ctx.agents_config_lock,
    )
    print(f"Added agent '{name}'. delegate_to_{name} is now available, and persists across restarts.")


async def _agents_remove(name: str, ctx: CLIContext) -> None:
    try:
        ctx.agent_registry.remove_worker(name)
    except AgentRegistryError as exc:
        print(str(exc))
        return
    ctx.leader_view.remove_extra_tool(f"delegate_to_{name}")
    await remove_agent_entry(ctx.settings.agents_config_path, name, ctx.agents_config_lock)
    print(f"Removed agent '{name}'.")


# --- /skills -----------------------------------------------------------------


async def _cmd_skills(rest: str, ctx: CLIContext) -> None:
    if not rest:
        _skills_list(ctx)
        return
    sub, _, arg = rest.partition(" ")
    arg = arg.strip()
    if sub == "load" and arg:
        await _skills_load(arg, ctx)
    elif sub == "install" and arg:
        await _skills_install(arg, ctx)
    else:
        print("Usage: /skills | /skills load <url> | /skills install <local_path>")


def _skills_list(ctx: CLIContext) -> None:
    specs_by_name = {s.name: s for s in ctx.registry.get_tool_specs()}
    for name in ctx.skill_loader.registered_skill_names:
        spec = specs_by_name.get(name)
        print(f"- {name}: {spec.description if spec else '(unknown)'}")


async def _skills_load(url: str, ctx: CLIContext) -> None:
    if not (url.startswith("http://") or url.startswith("https://")):
        print("Only http:// and https:// URLs are supported.")
        return
    try:
        response = await ctx.http_client.get(url, timeout=15.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - report to the human, don't crash the REPL
        print(f"Download failed: {exc}")
        return
    await _install_skill_package(response.content, ctx)


async def _skills_install(local_path: str, ctx: CLIContext) -> None:
    path = Path(local_path)
    if not path.is_file():
        print(f"No such file: '{local_path}'")
        return
    await _install_skill_package(path.read_bytes(), ctx)


async def _install_skill_package(data: bytes, ctx: CLIContext) -> None:
    try:
        skill_md_text, run_py_text = extract_skill_files(data)
        staged = stage_skill_install(skill_md_text, run_py_text, ctx.settings.skills_dir, ctx.registry)
    except SkillPackageError as exc:
        print(f"Rejected: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - a bad manifest, surfaced plainly
        print(f"Invalid skill package: {exc}")
        return

    print(f"\n--- SKILL.md ---\n{staged.skill_md_text}")
    print(f"--- run.py ---\n{staged.run_py_text}\n--- end of code ---")
    if staged.warnings:
        print("\nWARNING: static scan found potentially risky patterns — review carefully:")
        for warning in staged.warnings:
            print(f"  - {warning}")
    print(
        "\nThis code will run on your machine with the SAME PERMISSIONS as AuraAgent itself, "
        "every time this skill is called, with NO sandbox."
    )
    if input("Install this skill? [y/N]: ").strip().lower() != "y":
        print("Cancelled. No files were written.")
        return

    try:
        registered_name = finalize_skill_install(staged, ctx.skill_loader)
    except Exception as exc:  # noqa: BLE001 - a registration failure, surfaced plainly
        print(f"Failed to install: {exc}")
        return

    ctx.leader_view.add_allowed_pattern(registered_name)
    await add_capability(
        ctx.settings.agents_config_path, ctx.agent_registry.leader.name, registered_name, ctx.agents_config_lock
    )
    print(f"Installed and loaded skill '{registered_name}'. It persists across restarts.")
