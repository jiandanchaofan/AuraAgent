"""build_app_context — the ONE shared composition root every AuraAgent
frontend (the terminal REPL in main.py, and the GUI backend in gui/)
calls identically. This is what makes "CLI and GUI stay feature-equal" an
architectural fact rather than a promise two independently-maintained
implementations have to keep in sync by hand: both frontends get back the
exact same ToolRegistry, AgentRegistry, ScopedToolRegistryView, all six
self-extension tools, and CLIContext (so cli/commands.py's "/" dispatch —
/config /agents /skills — works identically from either frontend).

This function knows nothing about *how* a human will interact with the
result — no input()/print(), no WebSocket, no HTTP. It only differs by
the two things a frontend must supply itself, both constructed by the
caller BEFORE calling this (a ConfirmationChannel implementation needs a
logger to report decisions through, so the logger has to exist first):
  - `logger`: built from whichever LogSinks fit the frontend — e.g.
    AuraLogger([TerminalSink(), JSONLSink(dir)]) for the CLI,
    AuraLogger([JSONLSink(dir), WebSocketSink(...)]) for the GUI (see
    core/logger.py).
  - `confirmation_channel`: TerminalConfirmationChannel for the CLI, a
    WebSocket-backed one for the GUI (see confirmation/base.py — the
    interface itself never changes, only the implementation).

Everything else — which tools exist, which Agent can reach which tool,
how self-extension installs and persists things — is identical, because
it's literally the same code path building the same objects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from agents.agent_config_writer import add_capability
from agents.agent_registry import AgentRegistry
from agents.delegate_tool import build_delegate_tool
from agents.leader_worker_orchestrator import LeaderWorkerOrchestrator
from agents.scoped_tool_registry import ScopedToolRegistryView
from cli.context import CLIContext
from config.settings import Settings
from confirmation.base import ConfirmationChannel
from core.logger import AuraLogger
from core.react_engine import AsyncReActEngine
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.anthropic_provider import AnthropicProvider
from providers.base import LLMProvider
from providers.openai_provider import OpenAIProvider
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tools.base import RegisteredTool
from tools.calc.calculate_tool import register_calculate_tools
from tools.calendar.calendar_provider import CalendarProvider
from tools.calendar.calendar_tool import register_calendar_tools
from tools.calendar.google_auth import load_credentials as load_google_credentials
from tools.calendar.google_calendar_provider import GoogleCalendarProvider
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.calendar.swappable_calendar_provider import SwappableCalendarProvider
from tools.files.file_tool import register_file_tools
from tools.human.ask_human_tool import register_ask_human_tools
from tools.memory.memory_store import MemoryStore
from tools.memory.memory_tool import register_memory_tools
from tools.notes.notes_tool import register_notes_tools
from tools.profile.user_profile_store import UserProfileStore
from tools.profile.user_profile_tool import register_user_profile_tools
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.projects.project_tool import register_project_tools
from tools.registry import ToolRegistry
from tools.self_extend.find_capability_tool import register_find_capability_tool
from tools.self_extend.propose_agent_tool import register_propose_agent_tool
from tools.self_extend.propose_capability_grant_tool import register_propose_capability_grant_tool
from tools.self_extend.propose_external_skill_tool import register_propose_external_skill_tool
from tools.self_extend.propose_mcp_tool import register_propose_mcp_tool
from tools.self_extend.propose_skill_tool import register_propose_skill_tool
from tools.system.clipboard_tool import register_clipboard_tools
from tools.system.notification_tool import register_notification_tools
from tools.system.process_tool import register_process_tools
from tools.system.screenshot_tool import register_screenshot_tools
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_tool import register_task_tools
from tools.web.fetch_url_tool import build_default_http_client, register_fetch_url_tools, register_http_tools
from tools.workspace_root import SwappableWorkspaceRoot


@dataclass
class AppContext:
    settings: Settings
    registry: ToolRegistry
    agent_registry: AgentRegistry
    leader_view: ScopedToolRegistryView
    provider: SwappableProvider
    logger: AuraLogger
    orchestrator: LeaderWorkerOrchestrator
    skill_loader: SkillLoader
    mcp_manager: MCPClientManager
    http_client: httpx.AsyncClient
    agents_config_lock: asyncio.Lock
    mcp_config_lock: asyncio.Lock
    known_api_keys: dict[str, str]
    confirmation_channel: ConfirmationChannel
    workspace_root: SwappableWorkspaceRoot
    notes_root: SwappableWorkspaceRoot
    calendar_provider: SwappableCalendarProvider
    project_store: ProjectStore
    active_project: ActiveProjectState
    leader_engine: AsyncReActEngine
    base_leader_system_prompt: str
    cli_context: CLIContext

    async def aclose(self) -> None:
        """Release resources a frontend's shutdown path must await —
        mirrors main.py's `finally` block, factored out so the GUI
        backend doesn't have to remember the same two calls."""
        await self.http_client.aclose()
        await self.mcp_manager.close_all()


async def build_app_context(
    settings: Settings,
    confirmation_channel: ConfirmationChannel,
    logger: AuraLogger,
) -> AppContext:
    registry = ToolRegistry()

    # Shared mutable indirection (mirrors providers/swappable_provider.py's
    # exact pattern) so /workspace set (cli/commands.py) can repoint every
    # workspace-scoped tool at once, at runtime -- see tools/workspace_root.py.
    workspace_root = SwappableWorkspaceRoot(settings.workspace_root)
    # Same indirection, independent instance, for the notes sandbox -- /notes
    # set can repoint it (e.g. at a real Obsidian vault) without touching
    # workspace_root at all; the two are deliberately unrelated sandboxes.
    notes_root = SwappableWorkspaceRoot(settings.notes_sandbox_root)
    register_notes_tools(registry, notes_root)

    # Built here (rather than further down, where it originally lived) since
    # GoogleCalendarProvider below needs it -- it has no dependency on
    # anything above it, so moving it earlier is free.
    http_client = build_default_http_client()

    # Same SwappableProvider-style indirection as workspace_root/notes_root,
    # one level down -- /calendar connect (cli/commands.py) hot-swaps this to
    # a real GoogleCalendarProvider once OAuth succeeds, with zero changes to
    # calendar_tool.py (it only ever calls methods on `calendar_provider`).
    concrete_calendar: CalendarProvider
    if settings.calendar_backend == "google":
        # load_settings() already guarantees this file exists and is valid
        # before the app is allowed to start with calendar_backend=="google".
        credentials = load_google_credentials(settings.google_token_file)
        concrete_calendar = GoogleCalendarProvider(credentials, settings.google_token_file, http_client)
    else:
        concrete_calendar = LocalJSONCalendarProvider(settings.calendar_events_file)
    calendar_provider = SwappableCalendarProvider(concrete_calendar, settings.calendar_backend)
    register_calendar_tools(registry, calendar_provider, confirmation_channel)

    task_provider = LocalJSONTaskProvider(settings.tasks_file)
    register_task_tools(registry, task_provider, confirmation_channel)

    register_file_tools(registry, workspace_root, confirmation_channel)

    # Epic L2: system/desktop control -- clipboard/screenshot/process/
    # notification tools, same "local resource management" ownership as
    # tools/files/ (orchestrator directly, not delegated to a worker).
    register_clipboard_tools(registry, settings.clipboard_max_chars)
    register_screenshot_tools(registry, workspace_root, confirmation_channel)
    register_process_tools(registry, confirmation_channel)
    register_notification_tools(registry)

    memory_store = MemoryStore(settings.memory_file)
    register_memory_tools(registry, memory_store)
    register_ask_human_tools(registry, confirmation_channel)

    user_profile_store = UserProfileStore(settings.user_profile_file)
    register_user_profile_tools(registry, user_profile_store)

    # Project ("/project"): a directory (see workspace_root above -- /project
    # use hot-swaps it, exactly like /workspace set) + a bounded summary kept
    # OUTSIDE that directory. update_project_summary is Leader-only -- Workers
    # are never made aware a project is active at all (see the plan this was
    # built from: Project and Agent are deliberately orthogonal concepts).
    # register_project_tools() itself is called further below, once
    # leader_engine exists -- its handler needs to be able to live-sync
    # leader_engine.system_prompt after a mid-session summary update.
    project_store = ProjectStore(
        registry_path=settings.project_meta_dir / "registry.json",
        meta_dir=settings.project_meta_dir,
        summary_max_chars=settings.project_summary_max_chars,
    )
    active_project = ActiveProjectState()

    register_calculate_tools(registry)
    register_fetch_url_tools(
        registry, http_client, settings.fetch_url_timeout_seconds, settings.fetch_url_max_bytes
    )
    register_http_tools(
        registry, http_client, settings.fetch_url_timeout_seconds, settings.fetch_url_max_bytes,
        workspace_root, settings.download_max_bytes,
    )

    mcp_manager = MCPClientManager(settings.mcp_config_path, registry)
    await mcp_manager.connect_all()

    skill_loader = SkillLoader(
        settings.skills_dir, registry, settings.skill_timeout_seconds, workspace_root=workspace_root
    )
    skill_loader.scan_and_register()

    concrete_provider: LLMProvider
    if settings.llm_provider == "openai":
        concrete_provider = OpenAIProvider(
            api_key=settings.openai_api_key, model=settings.model_id, base_url=settings.openai_base_url
        )
    else:
        concrete_provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.model_id)
    # Wrapped in a SwappableProvider so every engine built below (Leader's,
    # every Worker's, and any built later by propose_new_agent or /agents
    # add) shares ONE mutable indirection point — this is what lets the
    # /config use CLI command (cli/commands.py) switch providers at runtime
    # without reaching into each engine individually.
    provider = SwappableProvider(concrete_provider, settings.llm_provider)

    # Multi-Agent composition: one shared ToolRegistry (built above) feeds a
    # ScopedToolRegistryView per agent (filtered by that agent's
    # `capabilities` from config/agents.json). Each Worker's AsyncReActEngine
    # is built once here and wrapped into a delegate_to_<name> tool that
    # exists ONLY in the Leader's own view — see agents/delegate_tool.py and
    # agents/scoped_tool_registry.py for why this keeps core/react_engine.py
    # completely unaware that Multi-Agent orchestration exists at all.
    agent_registry = AgentRegistry.load(settings.agents_config_path)

    leader_extra_tools: dict[str, RegisteredTool] = {}
    for worker in agent_registry.workers:
        worker_view = ScopedToolRegistryView(registry, worker.capabilities)
        worker_engine = AsyncReActEngine(
            provider=provider,
            registry=worker_view,
            logger=logger,
            system_prompt=worker.system_prompt,
            max_turns=settings.max_turns,
            agent_name=worker.name,
        )
        delegate_tool = build_delegate_tool(worker, worker_engine)
        leader_extra_tools[delegate_tool.spec.name] = delegate_tool

    leader = agent_registry.leader
    leader_view = ScopedToolRegistryView(registry, leader.capabilities, extra_tools=leader_extra_tools)

    # propose_new_skill/propose_new_agent/propose_mcp_server are Leader-only
    # self-extension tools (see tools/self_extend/) — registered into the
    # shared registry like any native tool (visibility still governed by
    # `capabilities` in config/agents.json). Each needs a way to widen the
    # Leader's OWN view once it installs something new, since a freshly
    # granted capability's name can't have been predicted by the static
    # capabilities list ahead of time — grant_access does that AND persists
    # the same grant to config/agents.json (agents/agent_config_writer.py),
    # so it survives a restart too, closing the gap the original
    # make_pptx-adoption investigation found (see that module's docstring).
    agents_config_lock = asyncio.Lock()
    mcp_config_lock = asyncio.Lock()

    async def grant_access(pattern: str) -> None:
        leader_view.add_allowed_pattern(pattern)
        await add_capability(settings.agents_config_path, leader.name, pattern, agents_config_lock)

    register_propose_skill_tool(
        registry, skill_loader, settings.skills_dir, confirmation_channel,
        grant_access=grant_access,
    )
    register_propose_agent_tool(
        registry, agent_registry, leader_view, provider, logger, settings.max_turns,
        settings.agents_config_path, agents_config_lock, confirmation_channel,
    )
    register_propose_mcp_tool(
        registry, mcp_manager, settings.mcp_config_path, mcp_config_lock, confirmation_channel,
        grant_access=grant_access,
    )

    # Epic K: autonomous discovery (find_capability, read-only) + two more
    # gated install paths (tools/self_extend/capability_search.py's module
    # docstring has the real, verified API shapes). find_capability never
    # installs anything itself — it just tells the Leader which of these
    # two (or propose_capability_grant, for something already installed)
    # to call next, with the details already filled in.
    register_find_capability_tool(registry, leader_view, http_client, settings.fetch_url_timeout_seconds)
    register_propose_capability_grant_tool(registry, leader_view, confirmation_channel, grant_access=grant_access)
    register_propose_external_skill_tool(
        registry, settings.skills_dir, skill_loader, http_client, settings.fetch_url_timeout_seconds,
        confirmation_channel, grant_access=grant_access,
    )

    # The user profile (tools/profile/) is read ONCE here and spliced
    # directly into the Leader's system prompt — unlike remember_fact/
    # recall_facts (pull-based, the LLM must actively query), the profile
    # is meant to be always visible with no tool call needed. See
    # tools/profile/user_profile_store.py's docstring for why a
    # mid-session update only takes effect starting the next restart.
    profile_text = (await user_profile_store.get_profile()).render()
    leader_system_prompt = f"{leader.system_prompt}\n\n{profile_text}" if profile_text else leader.system_prompt
    # Saved verbatim (no project summary spliced in yet -- no project is
    # active at startup) so /project use/none (cli/service.py) can rebuild
    # leader_engine.system_prompt as base + current project's summary
    # without ever losing or double-appending the profile text above.
    base_leader_system_prompt = leader_system_prompt

    leader_engine = AsyncReActEngine(
        provider=provider,
        registry=leader_view,
        logger=logger,
        system_prompt=leader_system_prompt,
        max_turns=settings.max_turns,
        agent_name=leader.name,
    )
    # Registered here (not up near the other stores) because the handler
    # needs leader_engine + base_leader_system_prompt to live-sync the
    # prompt after a mid-session summary update -- ScopedToolRegistryView
    # reads the shared registry live, so registering this late doesn't
    # make it any less visible to leader_view than an earlier-registered tool.
    register_project_tools(registry, project_store, active_project, leader_engine, base_leader_system_prompt)
    orchestrator = LeaderWorkerOrchestrator(leader_engine)

    # cli/commands.py's human-direct "/" commands (/config, /agents,
    # /skills, ...) are a second channel onto the same capabilities the
    # tools/self_extend/ LLM-proposal tools offer — see that module's
    # docstring. Bundled into AppContext (not just built inside main.py)
    # so the GUI backend can dispatch the exact same "/" commands too.
    # known_api_keys seeds from whatever Settings already loaded from
    # .env, so /config use works immediately for a key that was already
    # configured before this process started.
    known_api_keys = {"anthropic": settings.anthropic_api_key, "openai": settings.openai_api_key}
    cli_context = CLIContext(
        settings=settings,
        registry=registry,
        agent_registry=agent_registry,
        leader_view=leader_view,
        provider=provider,
        logger=logger,
        max_turns=settings.max_turns,
        skill_loader=skill_loader,
        mcp_manager=mcp_manager,
        http_client=http_client,
        agents_config_lock=agents_config_lock,
        workspace_root=workspace_root,
        notes_root=notes_root,
        calendar_provider=calendar_provider,
        project_store=project_store,
        active_project=active_project,
        leader_engine=leader_engine,
        base_leader_system_prompt=base_leader_system_prompt,
        known_api_keys=known_api_keys,
        env_file_path=settings.env_file_path,
    )

    return AppContext(
        settings=settings,
        registry=registry,
        agent_registry=agent_registry,
        leader_view=leader_view,
        provider=provider,
        logger=logger,
        orchestrator=orchestrator,
        skill_loader=skill_loader,
        mcp_manager=mcp_manager,
        http_client=http_client,
        agents_config_lock=agents_config_lock,
        mcp_config_lock=mcp_config_lock,
        known_api_keys=known_api_keys,
        confirmation_channel=confirmation_channel,
        workspace_root=workspace_root,
        notes_root=notes_root,
        calendar_provider=calendar_provider,
        project_store=project_store,
        active_project=active_project,
        leader_engine=leader_engine,
        base_leader_system_prompt=base_leader_system_prompt,
        cli_context=cli_context,
    )
