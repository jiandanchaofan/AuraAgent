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
from tools.scheduler.cron_utils import describe_schedule
from tools.tasks import google_tasks_auth

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
  /notes                           Show the current notes root directory and quick notes subdirectory
  /notes set <path>                Switch the notes root, e.g. an Obsidian vault (persists to .env)
  /notes quickdir <path>           Set which subdirectory under notes root holds daily notes (e.g. 'Daily Notes', for Thino-style quick notes)
  /calendar                        Show the current calendar backend (local or google)
  /calendar connect                Connect a real Google Calendar (opens a browser for OAuth consent)
  /calendar disconnect             Switch back to the local JSON calendar
  /tasks                            Show the current tasks backend (local or google)
  /tasks connect                   Connect your real Google Tasks account (opens a browser for OAuth consent)
  /tasks disconnect                Switch back to the local JSON task list
  /project                         Show the active project (if any) and list all projects
  /project list                    List all projects
  /project create <slug>           Create a project (auto directory under sandbox/projects/<slug>)
  /project create <slug> <path>    Create a project pointed at an existing directory
  /project use <slug>              Enter a project (repoints the workspace, loads its summary) -- session-only
  /project none                    Leave the active project, falling back to /workspace set's default
  /project rename <slug> <name>    Rename a project's display name (its slug/directory don't change)
  /project role <slug> <text>      Set/replace a project's Role (persona/instructions), user-editable any time
  /project state <slug> <text>     Set/replace a project's current-progress summary, user-editable any time
  /project memory <slug>           List a project's own remembered facts
  /project memory <slug> add <text>          Add a fact to a project's memory
  /project memory <slug> edit <id> <text>    Edit an existing fact
  /project memory <slug> delete <id>         Delete a fact
  /project tools <slug>            List a project's enabled Skill/MCP tools and what else is available
  /project tools <slug> enable <pattern>     Enable an available Skill/MCP tool for this project only
  /project tools <slug> disable <pattern>    Disable it again
  /mcp                             List connected MCP servers and how many tools each contributed
  /schedule                        List all scheduled tasks (once or recurring), across every project
  /schedule add once <ISO时间> [project <slug>] <任务文本>             Run a task once at a specific time
  /schedule add cron <分> <时> <日> <月> <周> [project <slug>] <任务文本>  Run a task on a recurring schedule
  /schedule edit <id> once|cron|project|task <值>   Change one aspect of an existing schedule
  /schedule pause|resume <id>      Temporarily disable/re-enable a schedule
  /schedule delete <id>            Delete a schedule
  /device                         List registered devices (remote access, e.g. Auralis)
  /device register <name>         Register a new device and print its one-time token
  /device revoke <id>             Revoke a device's access
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
        await _cmd_workspace(rest, ctx)
    elif command == "/notes":
        _cmd_notes(rest, ctx)
    elif command == "/calendar":
        _cmd_calendar(rest, ctx)
    elif command == "/tasks":
        await _cmd_tasks(rest, ctx)
    elif command == "/project":
        await _cmd_project(rest, ctx)
    elif command == "/mcp":
        _cmd_mcp(ctx)
    elif command == "/schedule":
        await _cmd_schedule(rest, ctx)
    elif command == "/device":
        await _cmd_device(rest, ctx)
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


async def _cmd_workspace(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_workspace_status(ctx)
        if status.path == status.default:
            print(f"workspace={status.path}")
        else:
            print(f"workspace={status.path} (a Project is active; default={status.default})")
        return
    sub, _, raw_path = rest.partition(" ")
    raw_path = raw_path.strip()
    if sub == "set" and raw_path:
        # partition, not rest.split() — a Windows path can itself contain
        # spaces ("/workspace set C:\Users\me\My Documents"), same
        # reasoning as /skills install's own use of partition above.
        try:
            update = await service.set_workspace_root(raw_path, ctx)
        except ValueError as exc:
            print(str(exc))
            return
        if update.deferred:
            print(
                f"Default workspace set to '{update.default}'. Saved to .env — persists across "
                "restarts. A Project is currently active, so this won't take effect until /project none."
            )
        else:
            print(f"Workspace set to '{update.default}'. Saved to .env — persists across restarts.")
    else:
        print("Usage: /workspace | /workspace set <path>")


# --- /notes ------------------------------------------------------------------


def _cmd_notes(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_notes_root_status(ctx)
        print(f"notes={status.path}")
        print(f"quick notes subdir={status.quick_notes_subdir} (under notes root, e.g. for Obsidian's Thino)")
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
    elif sub == "quickdir" and raw_path:
        try:
            resolved = service.set_quick_notes_subdir(raw_path, ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Quick notes subdirectory set to '{resolved}'. Saved to .env — persists across restarts.")
    else:
        print("Usage: /notes | /notes set <path> | /notes quickdir <relative path under notes root>")


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


# --- /tasks ----------------------------------------------------------------


async def _cmd_tasks(rest: str, ctx: CLIContext) -> None:
    if not rest:
        status = service.get_task_backend_status(ctx)
        print(f"backend={status.backend}")
        return
    if rest == "connect":
        try:
            service.check_google_tasks_client_secret(ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print("Opening a browser to connect your Google Tasks account...")
        try:
            google_tasks_auth.run_oauth_flow(ctx.settings.google_client_secret_file, ctx.settings.google_tasks_token_file)
        except Exception as exc:  # noqa: BLE001 - report cleanly, don't crash the REPL on a failed OAuth flow
            print(f"Google authorization failed: {exc}")
            return
        try:
            await service.finish_google_tasks_connect(ctx)
        except ValueError as exc:
            print(str(exc))
            return
        print("Connected to Google Tasks ('AuraAgent' list). Now active immediately -- no restart needed.")
    elif rest == "disconnect":
        service.disconnect_google_tasks(ctx)
        print("Disconnected. Back to the local task list. (Saved token was kept, in case you reconnect later.)")
    else:
        print("Usage: /tasks | /tasks connect | /tasks disconnect")


# --- /mcp --------------------------------------------------------------------


def _cmd_mcp(ctx: CLIContext) -> None:
    servers = service.list_mcp_servers(ctx)
    if not servers:
        print("No MCP servers connected.")
        return
    for server in servers:
        print(f"  {server.name} -- {server.tool_count} tool(s)")


# --- /project --------------------------------------------------------------


async def _cmd_project(rest: str, ctx: CLIContext) -> None:
    if not rest or rest == "list":
        status = await service.get_project_status(ctx)
        if not status.projects:
            print("No projects yet. Use /project create <slug> to make one.")
            return
        for project in status.projects:
            marker = "*" if project.slug == status.active_slug else " "
            label = project.slug if project.name == project.slug else f"{project.slug} ({project.name})"
            print(f"{marker} {label} -- {project.directory}")
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
        print(f"Left the active project. Workspace back to the default ({ctx.workspace_root.current}).")
    elif sub == "rename" and arg:
        # partition, not arg.split() -- a project name can itself contain
        # spaces, same reasoning as /project create's optional path.
        slug, _, new_name = arg.partition(" ")
        new_name = new_name.strip()
        if not new_name:
            print("Usage: /project rename <slug> <name>")
            return
        try:
            project = await service.rename_project(ctx, slug, new_name)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Renamed '{project.slug}' to '{project.name}'.")
    elif sub == "role" and arg:
        # partition, not arg.split() -- a role description is free text
        # and can itself contain spaces, same reasoning as /project rename.
        slug, _, role_text = arg.partition(" ")
        role_text = role_text.strip()
        if not role_text:
            print("Usage: /project role <slug> <text>")
            return
        try:
            await service.set_project_role(ctx, slug, role_text)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Updated role for '{slug}'.")
    elif sub == "state" and arg:
        # partition, not arg.split() -- same reasoning as /project role.
        slug, _, state_text = arg.partition(" ")
        state_text = state_text.strip()
        if not state_text:
            print("Usage: /project state <slug> <text>")
            return
        try:
            await service.set_project_current_state(ctx, slug, state_text)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Updated current state for '{slug}'.")
    elif sub == "memory" and arg:
        slug, _, remainder = arg.partition(" ")
        remainder = remainder.strip()
        action, _, action_arg = remainder.partition(" ")
        action_arg = action_arg.strip()
        try:
            if not remainder or action == "list":
                facts = await service.list_project_memory(ctx, slug)
                if not facts:
                    print(f"No memory recorded yet for '{slug}'.")
                else:
                    for fact in facts:
                        print(f"  (id={fact.id}) {fact.content}")
            elif action == "add" and action_arg:
                fact = await service.add_project_memory(ctx, slug, action_arg)
                print(f"Remembered (id={fact.id}): {fact.content}")
            elif action == "delete" and action_arg:
                await service.delete_project_memory(ctx, slug, action_arg)
                print(f"Deleted fact '{action_arg}' from '{slug}'.")
            elif action == "edit" and action_arg:
                # partition again -- the edited content can itself contain spaces.
                fact_id, _, content = action_arg.partition(" ")
                content = content.strip()
                if not content:
                    print("Usage: /project memory <slug> edit <id> <text>")
                    return
                fact = await service.update_project_memory(ctx, slug, fact_id, content)
                print(f"Updated (id={fact.id}): {fact.content}")
            else:
                print("Usage: /project memory <slug> [add <text> | edit <id> <text> | delete <id>]")
        except ValueError as exc:
            print(str(exc))
    elif sub == "tools" and arg:
        slug, _, remainder = arg.partition(" ")
        remainder = remainder.strip()
        project = await ctx.project_store.get_project(slug)
        if project is None:
            print(f"No project named '{slug}'.")
            return
        action, _, pattern = remainder.partition(" ")
        pattern = pattern.strip()
        if not remainder or action == "list":
            candidates = service.get_available_project_tools(ctx)
            enabled = ", ".join(project.enabled_tools) if project.enabled_tools else "(none)"
            print(f"Enabled for '{slug}': {enabled}")
            if candidates:
                print("Available to enable:")
                for candidate in candidates:
                    mark = "*" if candidate.pattern in project.enabled_tools else " "
                    print(f"  {mark} {candidate.pattern} ({candidate.source}: {candidate.label})")
            else:
                print("Nothing additional available to enable (everything installed is already globally granted).")
            return
        if action == "enable" and pattern:
            new_patterns = list(project.enabled_tools)
            if pattern not in new_patterns:
                new_patterns.append(pattern)
            await service.set_project_tools(ctx, slug, new_patterns)
            print(f"Enabled '{pattern}' for project '{slug}'.")
        elif action == "disable" and pattern:
            new_patterns = [p for p in project.enabled_tools if p != pattern]
            await service.set_project_tools(ctx, slug, new_patterns)
            print(f"Disabled '{pattern}' for project '{slug}'.")
        else:
            print("Usage: /project tools <slug> [list | enable <pattern> | disable <pattern>]")
    else:
        print(
            "Usage: /project | /project list | /project create <slug> [path] | /project use <slug> | "
            "/project none | /project rename <slug> <name> | /project role <slug> <text> | "
            "/project state <slug> <text> | /project memory <slug> [add|edit|delete ...] | "
            "/project tools <slug> [enable|disable <pattern>]"
        )


# --- /schedule ---------------------------------------------------------------
# N8 proactivity (tools/scheduler/): tasks that run without a human
# re-triggering them, once or on a schedule. Centralized here (not nested
# under /project) -- a schedule optionally names a project, but managing
# it never requires being "inside" that project. Direct human action, no
# confirmation gate, same precedent as /project tools' enable/disable.

_SCHEDULE_USAGE = (
    "Usage: /schedule | "
    "/schedule add once <ISO时间> [project <slug>] <任务文本> | "
    "/schedule add cron <分> <时> <日> <月> <周> [project <slug>] <任务文本> | "
    "/schedule edit <id> once <ISO时间> | cron <分> <时> <日> <月> <周> | project <slug>|none | task <新文本> | "
    "/schedule pause <id> | /schedule resume <id> | /schedule delete <id>"
)


def _parse_add_target(rest2: str) -> tuple[str | None, str]:
    """Splits the tail of an `/schedule add ...` command into an optional
    `project <slug>` prefix and the remaining task text."""
    rest2 = rest2.strip()
    if rest2.startswith("project "):
        _, _, after = rest2.partition(" ")
        slug, _, task_text = after.partition(" ")
        return slug, task_text.strip()
    return None, rest2


async def _cmd_schedule(rest: str, ctx: CLIContext) -> None:
    if not rest:
        schedules = await service.list_schedules(ctx)
        if not schedules:
            print("No schedules yet. Use /schedule add once|cron ... to create one.")
            return
        for s in schedules:
            marker = " " if s.enabled else "x"
            project_label = s.project_slug or "(unaffiliated)"
            desc = describe_schedule(s.trigger_type, s.run_at, s.cron_expression)
            print(f"[{marker}] {s.id} | {project_label} | {desc} | {s.task}")
        return

    sub, _, arg = rest.partition(" ")
    arg = arg.strip()

    if sub == "add" and arg:
        kind, _, remainder = arg.partition(" ")
        remainder = remainder.strip()
        try:
            if kind == "once":
                run_at, _, rest2 = remainder.partition(" ")
                project_slug, task_text = _parse_add_target(rest2)
                if not task_text:
                    print(_SCHEDULE_USAGE)
                    return
                schedule = await service.create_once_schedule(ctx, run_at, task_text, project_slug)
            elif kind == "cron":
                tokens = remainder.split(" ", maxsplit=5)
                if len(tokens) < 6:
                    print(_SCHEDULE_USAGE)
                    return
                cron_expression = " ".join(tokens[:5])
                project_slug, task_text = _parse_add_target(tokens[5])
                if not task_text:
                    print(_SCHEDULE_USAGE)
                    return
                schedule = await service.create_cron_schedule(ctx, cron_expression, task_text, project_slug)
            else:
                print(_SCHEDULE_USAGE)
                return
        except ValueError as exc:
            print(str(exc))
            return
        desc = describe_schedule(schedule.trigger_type, schedule.run_at, schedule.cron_expression)
        print(f"Scheduled (id={schedule.id}): {desc} -- {schedule.task}")
    elif sub == "edit" and arg:
        schedule_id, _, remainder = arg.partition(" ")
        field, _, value = remainder.partition(" ")
        value = value.strip()
        if not value:
            print(_SCHEDULE_USAGE)
            return
        try:
            if field == "once":
                await service.edit_schedule(ctx, schedule_id, run_at=value)
            elif field == "cron":
                tokens = value.split(" ")
                if len(tokens) != 5:
                    print(_SCHEDULE_USAGE)
                    return
                await service.edit_schedule(ctx, schedule_id, cron_expression=value)
            elif field == "project":
                await service.edit_schedule(ctx, schedule_id, project_slug=None if value == "none" else value)
            elif field == "task":
                await service.edit_schedule(ctx, schedule_id, task=value)
            else:
                print(_SCHEDULE_USAGE)
                return
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Updated '{schedule_id}'.")
    elif sub == "pause" and arg:
        try:
            await service.set_schedule_enabled(ctx, arg, False)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Paused '{arg}'.")
    elif sub == "resume" and arg:
        try:
            await service.set_schedule_enabled(ctx, arg, True)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Resumed '{arg}'.")
    elif sub == "delete" and arg:
        try:
            await service.delete_schedule(ctx, arg)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Deleted '{arg}'.")
    else:
        print(_SCHEDULE_USAGE)


# --- /device (N14 -- Auralis / remote access) -------------------------------

_DEVICE_USAGE = "Usage: /device | /device register <name> | /device revoke <id>"


async def _cmd_device(rest: str, ctx: CLIContext) -> None:
    if not rest:
        devices = await service.list_devices(ctx)
        if not devices:
            print("No devices registered yet. Use /device register <name> to add one.")
            return
        for d in devices:
            last_seen = d.last_seen_at or "never"
            print(f"{d.id} | {d.name} | last seen: {last_seen}")
        return

    sub, _, arg = rest.partition(" ")
    arg = arg.strip()

    if sub == "register" and arg:
        device, raw_token = await service.create_device(ctx, arg)
        print(f"Registered device '{device.name}' (id={device.id}).")
        print(f"Token (shown once, will not be shown again): {raw_token}")
        print(
            "Give this token to the device's own client config. It only works once "
            "AURA_REQUIRE_AUTH=1 is set -- see .env.example."
        )
    elif sub == "revoke" and arg:
        try:
            await service.revoke_device(ctx, arg)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Revoked '{arg}'.")
    else:
        print(_DEVICE_USAGE)
