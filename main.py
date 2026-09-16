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

from config.settings import load_settings
from confirmation.terminal_channel import TerminalConfirmationChannel
from core.logger import AuraLogger, print_banner
from core.react_engine import AsyncReActEngine
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.anthropic_provider import AnthropicProvider
from providers.base import LLMProvider
from providers.openai_provider import OpenAIProvider
from tools.calc.calculate_tool import register_calculate_tools
from tools.calendar.calendar_tool import register_calendar_tools
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.human.ask_human_tool import register_ask_human_tools
from tools.memory.memory_store import MemoryStore
from tools.memory.memory_tool import register_memory_tools
from tools.notes.notes_tool import register_notes_tools
from tools.registry import ToolRegistry
from tools.tasks.local_json_task_provider import LocalJSONTaskProvider
from tools.tasks.task_tool import register_task_tools
from tools.web.fetch_url_tool import build_default_http_client, register_fetch_url_tools

SYSTEM_PROMPT = (
    "You are AuraAgent, a personal AI assistant with access to a sandboxed "
    "Markdown note-taking system, a local calendar, and a task list. Use "
    "the available tools to help the user manage their notes, schedule, "
    "and to-dos. Think step by step, and only call a tool when you need "
    "information or an action you can't provide from your own knowledge. "
    "Deleting a task, or deleting/modifying an important calendar event, "
    "may pause to ask the user for confirmation — if declined, treat it "
    "as a normal outcome and report it back plainly.\n\n"
    "You have long-term memory tools: remember_fact and recall_facts. Facts "
    "you save are NOT shown to you automatically — you must call "
    "recall_facts yourself, especially before telling the user you don't "
    "know a preference, prior decision, or detail they may have told you in "
    "an earlier session. Call remember_fact only for durable, reusable "
    "information (stated preferences, standing facts, decisions) — not for "
    "one-off task details already captured via notes, tasks, or calendar. "
    "Do not call recall_facts reflexively on every turn; only when it's "
    "plausibly relevant.\n\n"
    "You also have ask_human, which pauses and asks the user an open-ended "
    "question. Use it only when proceeding without clarification risks an "
    "incorrect or unsafe action; prefer recall_facts, search_notes, or a "
    "clearly-stated reasonable assumption over asking."
)


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

    register_calculate_tools(registry)
    http_client = build_default_http_client()
    register_fetch_url_tools(
        registry, http_client, settings.fetch_url_timeout_seconds, settings.fetch_url_max_bytes
    )

    mcp_manager = MCPClientManager(settings.mcp_config_path, registry)
    await mcp_manager.connect_all()
    # Skill registration lands in a later iteration — see skills/ for its
    # scaffolded seam.

    provider: LLMProvider
    if settings.llm_provider == "openai":
        provider = OpenAIProvider(
            api_key=settings.openai_api_key, model=settings.model_id, base_url=settings.openai_base_url
        )
    else:
        provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.model_id)
    logger = AuraLogger(settings.logs_dir)
    engine = AsyncReActEngine(
        provider=provider,
        registry=registry,
        logger=logger,
        system_prompt=SYSTEM_PROMPT,
        max_turns=settings.max_turns,
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
            try:
                await engine.run(user_input)
            except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
                print(f"[ERROR] {type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()
        await mcp_manager.close_all()


if __name__ == "__main__":
    asyncio.run(main())
