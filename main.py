"""AuraAgent CLI entrypoint and composition root.

This is the ONLY module that imports every concrete implementation
(the Anthropic provider, the notes tools, sandbox paths, ...) and wires
them into the abstract interfaces core/react_engine.py depends on. Every
other module reaches its collaborators only through an interface, which
is what keeps this "plugin-first": adding a new tool or swapping the LLM
provider means editing this file, not core/react_engine.py.

Run with: python main.py
"""
from __future__ import annotations

import asyncio

from agents.agent_config_writer import add_capability
from agents.agent_registry import AgentRegistry
from agents.delegate_tool import build_delegate_tool
from agents.leader_worker_orchestrator import LeaderWorkerOrchestrator
from agents.scoped_tool_registry import ScopedToolRegistryView
from cli.commands import dispatch_command, is_command
from cli.context import CLIContext
from config.settings import PROJECT_ROOT, load_settings
from confirmation.terminal_channel import TerminalConfirmationChannel
from core.logger import AuraLogger, print_banner
from core.react_engine import AsyncReActEngine
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.anthropic_provider import AnthropicProvider
from providers.base import LLMProvider
from providers.openai_provider import OpenAIProvider
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tools.base import RegisteredTool
from tools.calc.calculate_tool import register_calculate_tools
from tools.calendar.calendar_tool import register_calendar_tools
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.human.ask_human_tool import register_ask_human_tools
from tools.memory.memory_store import MemoryStore
from tools.memory.memory_tool import register_memory_tools
from tools.notes.notes_tool import register_notes_tools
from tools.profile.user_profile_store import UserProfileStore
from tools.profile.user_profile_tool import register_user_profile_tools
from tools.registry import ToolRegistry
from tools.self_extend.propose_agent_tool import register_propose_agent_tool
from tools.self_extend.propose_mcp_tool import register_propose_mcp_tool
from tools.self_extend.propose_skill_tool import register_propose_skill_tool
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_tool import register_task_tools
from tools.web.fetch_url_tool import build_default_http_client, register_fetch_url_tools


async def main() -> None:
    settings = load_settings()

    registry = ToolRegistry()
    register_notes_tools(registry, settings.notes_sandbox_root)

    calendar_provider = LocalJSONCalendarProvider(settings.calendar_events_file)
    confirmation_channel = TerminalConfirmationChannel()
    register_calendar_tools(registry, calendar_provider, confirmation_channel)

    task_provider = LocalJSONTaskProvider(settings.tasks_file)
    register_task_tools(registry, task_provider, confirmation_channel)

    memory_store = MemoryStore(settings.memory_file)
    register_memory_tools(registry, memory_store)
    register_ask_human_tools(registry, confirmation_channel)

    user_profile_store = UserProfileStore(settings.user_profile_file)
    register_user_profile_tools(registry, user_profile_store)

    register_calculate_tools(registry)
    http_client = build_default_http_client()
    register_fetch_url_tools(
        registry, http_client, settings.fetch_url_timeout_seconds, settings.fetch_url_max_bytes
    )

    mcp_manager = MCPClientManager(settings.mcp_config_path, registry)
    await mcp_manager.connect_all()

    skill_loader = SkillLoader(settings.skills_dir, registry, settings.skill_timeout_seconds)
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
    logger = AuraLogger(settings.logs_dir)

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

    # The user profile (tools/profile/) is read ONCE here and spliced
    # directly into the Leader's system prompt — unlike remember_fact/
    # recall_facts (pull-based, the LLM must actively query), the profile
    # is meant to be always visible with no tool call needed. See
    # tools/profile/user_profile_store.py's docstring for why a
    # mid-session update only takes effect starting the next restart.
    profile_text = (await user_profile_store.get_profile()).render()
    leader_system_prompt = f"{leader.system_prompt}\n\n{profile_text}" if profile_text else leader.system_prompt

    leader_engine = AsyncReActEngine(
        provider=provider,
        registry=leader_view,
        logger=logger,
        system_prompt=leader_system_prompt,
        max_turns=settings.max_turns,
        agent_name=leader.name,
    )
    orchestrator = LeaderWorkerOrchestrator(leader_engine)

    # cli/commands.py's human-direct "/" commands (/config, /agents,
    # /skills, ...) are a second channel onto the same capabilities the
    # tools/self_extend/ LLM-proposal tools offer — see that module's
    # docstring. known_api_keys seeds from whatever Settings already loaded
    # from .env, so /config use works immediately for a key that was
    # already configured before this process started.
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
        known_api_keys=known_api_keys,
        env_file_path=PROJECT_ROOT / ".env",
    )

    print_banner(f"AuraAgent, developed by James Jiang | provider={settings.llm_provider} model={settings.model_id} | type 'exit' to quit")
    try:
        while True:
            try:
                user_input = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break
            if is_command(user_input):
                try:
                    await dispatch_command(user_input, cli_context)
                except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
                    print(f"[ERROR] {type(exc).__name__}: {exc}")
                continue
            try:
                await orchestrator.run(user_input)
            except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
                print(f"[ERROR] {type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()
        await mcp_manager.close_all()


if __name__ == "__main__":
    asyncio.run(main())
