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
still reuse the exact same construction/persistence helpers via
cli/service.py (agents/agent_builder.py, agents/agent_config_writer.py,
skills/skill_package.py) so both channels stay consistent.

Every handler here is deliberately thin: it collects input (input()/
getpass()), calls a cli/service.py function to do the actual work, and
prints the result — the "how do I ask/display" half of each command.
gui/routes.py (Epic M3) is the GUI's counterpart, calling the exact same
cli/service.py functions for its "how do I ask/display over HTTP" half —
see that module's docstring, and cli/service.py's, for the split.

Nothing here ever touches core/react_engine.py, the LLM, or
logs/session-*.jsonl — this is why /config set-key is safe: an API key
entered here is written straight to .env and an in-memory dict, never
passed through anything that logs or shows content to the LLM.
"""
from __future__ import annotations

import getpass

from agents.agent_definition import AgentDefinitionError
from agents.agent_registry import AgentRegistryError
from cli import service
from cli.context import CLIContext
from skills.skill_package import SkillPackageError
from skills.skill_schema import SkillManifestError
from tools.calendar import google_auth

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
  /workspace                       Show the current workspace root directory
  /workspace set <path>            Switch the workspace root (persists to .env)
  /notes                           Show the current notes root directory
  /notes set <path>                Switch the notes root, e.g. an Obsidian vault (persists to .env)
  /calendar                        Show the current calendar backend (local or google)
  /calendar connect                Connect a real Google Calendar (opens a browser for OAuth consent)
  /calendar disconnect             Switch back to the local JSON calendar
  /project                         Show the active project (if any) and list all projects
  /project list                    List all projects
  /project create <slug>           Create a project (auto directory under sandbox/projects/<slug>)
  /project create <slug> <path>    Create a project pointed at an existing directory
  /project use <slug>              Enter a project (repoints the workspace, loads its summary) -- session-only
  /project none                    Leave the active project, restoring the workspace from before it
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
    elif command == "/workspace":
        _cmd_workspace(rest, ctx)
    elif command == "/notes":
        _cmd_notes(rest, ctx)
    elif command == "/calendar":
        _cmd_calendar(rest, ctx)
    elif command == "/project":
        await _cmd_project(rest, ctx)
    else:
        print(f"Unknown command '{command}'. Type /help for a list of commands.")


# --- /config ---------------------------------------------------------------


async def _cmd_config(args: list[str], ctx: CLIContext) -> None:
    if not args:
        status = service.get_config_status(ctx)
        print(f"provider={status.provider} model={status.model} key={status.key_masked}")
    elif args[0] == "use" and len(args) >= 3:
        try:
            service.switch_provider(args[1], args[2], ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Switched to provider={args[1]} model={args[2]}.")
    elif args[0] == "set-key" and len(args) >= 2:
        provider_name = args[1]
        if provider_name not in service.KNOWN_PROVIDERS:
            print(f"Unknown provider '{provider_name}' — expected 'anthropic' or 'openai'.")
            return
        value = getpass.getpass(f"Enter API key for '{provider_name}' (input hidden): ")
        try:
            service.set_api_key(provider_name, value, ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Saved. Run '/config use {provider_name} <model_id>' to switch to it now.")
    else:
        print("Usage: /config | /config use <anthropic|openai> <model_id> | /config set-key <anthropic|openai>")


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
    for agent in service.list_agents(ctx):
        print(f"- {agent.name} ({agent.role}): {', '.join(agent.capabilities)}")


async def _agents_add(ctx: CLIContext) -> None:
    name = input("New agent name: ").strip()
    system_prompt = input("System prompt: ").strip()
    caps_raw = input("Capabilities (comma-separated tool name patterns, e.g. calculate,*task*): ").strip()
    capabilities = [c.strip() for c in caps_raw.split(",") if c.strip()]

    try:
        agent_def = service.validate_new_agent(name, system_prompt, capabilities, ctx)
    except AgentDefinitionError as exc:
        print(f"Invalid agent definition: {exc}")
        return
    except ValueError as exc:
        print(str(exc))
        return

    print(f"\nAbout to add worker '{name}':")
    print(f"  system_prompt: {system_prompt}")
    print(f"  capabilities: {capabilities}")
    if input("Proceed? [y/N]: ").strip().lower() != "y":
        print("Cancelled.")
        return

    await service.commit_new_agent(agent_def, ctx)
    print(f"Added agent '{name}'. delegate_to_{name} is now available, and persists across restarts.")


async def _agents_remove(name: str, ctx: CLIContext) -> None:
    try:
        await service.remove_agent(name, ctx)
    except AgentRegistryError as exc:
        print(str(exc))
        return
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
    for skill in service.list_skills(ctx):
        print(f"- {skill.name}: {skill.description}")


async def _skills_load(url: str, ctx: CLIContext) -> None:
    try:
        staged = await service.stage_skill_from_url(url, ctx)
    except ValueError as exc:
        print(str(exc))
        return
    except (SkillPackageError, SkillManifestError) as exc:
        print(f"Rejected: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - a network/HTTP failure, report to the human
        print(f"Download failed: {exc}")
        return
    await _review_and_install(staged, ctx)


async def _skills_install(local_path: str, ctx: CLIContext) -> None:
    try:
        staged = service.stage_skill_from_path(local_path, ctx)
    except FileNotFoundError as exc:
        print(str(exc))
        return
    except (SkillPackageError, SkillManifestError) as exc:
        print(f"Rejected: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - a bad manifest, surfaced plainly
        print(f"Invalid skill package: {exc}")
        return
    await _review_and_install(staged, ctx)


async def _review_and_install(staged, ctx: CLIContext) -> None:
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
        registered_name = await service.commit_skill(staged, ctx)
    except Exception as exc:  # noqa: BLE001 - a registration failure, surfaced plainly
        print(f"Failed to install: {exc}")
        return
    print(f"Installed and loaded skill '{registered_name}'. It persists across restarts.")


# --- /workspace --------------------------------------------------------------


def _cmd_workspace(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_workspace_status(ctx)
        print(f"workspace={status.path}")
        return
    sub, _, raw_path = rest.partition(" ")
    raw_path = raw_path.strip()
    if sub == "set" and raw_path:
        # partition, not rest.split() — a Windows path can itself contain
        # spaces ("/workspace set C:\Users\me\My Documents"), same
        # reasoning as /skills install's own use of partition above.
        try:
            resolved = service.set_workspace_root(raw_path, ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Workspace set to '{resolved}'. Saved to .env — persists across restarts.")
    else:
        print("Usage: /workspace | /workspace set <path>")


# --- /notes ------------------------------------------------------------------


def _cmd_notes(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_notes_root_status(ctx)
        print(f"notes={status.path}")
        return
    sub, _, raw_path = rest.partition(" ")
    raw_path = raw_path.strip()
    if sub == "set" and raw_path:
        try:
            resolved = service.set_notes_root(raw_path, ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Notes root set to '{resolved}'. Saved to .env — persists across restarts.")
    else:
        print("Usage: /notes | /notes set <path>")


# --- /calendar -----------------------------------------------------------


def _cmd_calendar(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_calendar_status(ctx)
        print(f"backend={status.backend}")
        return
    if rest == "connect":
        try:
            service.check_google_client_secret(ctx)
        except ValueError as exc:
            print(str(exc))
            return
        # Blocking, opens a browser and waits for the user to approve the
        # consent screen -- deliberately done here (cli/commands.py), not in
        # cli/service.py, which never does blocking/interactive I/O; same
        # split /config set-key's getpass.getpass() call already follows.
        print("Opening a browser to connect your Google Calendar...")
        try:
            google_auth.run_oauth_flow(ctx.settings.google_client_secret_file, ctx.settings.google_token_file)
        except Exception as exc:  # noqa: BLE001 - report cleanly, don't crash the REPL on a failed OAuth flow
            print(f"Google authorization failed: {exc}")
            return
        try:
            service.finish_google_calendar_connect(ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print("Connected to Google Calendar. Now active immediately -- no restart needed.")
    elif rest == "disconnect":
        service.disconnect_google_calendar(ctx)
        print("Disconnected. Back to the local calendar. (Saved token was kept, in case you reconnect later.)")
    else:
        print("Usage: /calendar | /calendar connect | /calendar disconnect")


# --- /project --------------------------------------------------------------


async def _cmd_project(rest: str, ctx: CLIContext) -> None:
    if not rest or rest == "list":
        status = await service.get_project_status(ctx)
        if not status.projects:
            print("No projects yet. Use /project create <slug> to make one.")
            return
        for project in status.projects:
            marker = "*" if project.slug == status.active_slug else " "
            print(f"{marker} {project.slug} -- {project.directory}")
        if status.active_slug is None:
            print("(no project active)")
        return

    sub, _, arg = rest.partition(" ")
    arg = arg.strip()

    if sub == "create" and arg:
        # partition, not arg.split() -- the optional path can itself
        # contain spaces on Windows, same reasoning as /workspace set.
        slug, _, path = arg.partition(" ")
        path = path.strip() or None
        try:
            project = await service.create_project(ctx, slug, path)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Created project '{project.slug}' at '{project.directory}'. Use /project use {project.slug} to enter it.")
    elif sub == "use" and arg:
        try:
            project = await service.use_project(ctx, arg)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Entered project '{project.slug}' ({project.directory}). Active immediately, no restart needed.")
    elif rest == "none":
        await service.exit_project(ctx)
        print("Left the active project. Workspace restored to what it was before.")
    else:
        print("Usage: /project | /project list | /project create <slug> [path] | /project use <slug> | /project none")
