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
from core.logger import AuraLogger
from core.react_engine import AsyncReActEngine
from providers.anthropic_provider import AnthropicProvider
from tools.calendar.calendar_tool import register_calendar_tools
from tools.calendar.local_json_calendar import LocalJSONCalendarProvider
from tools.notes.notes_tool import register_notes_tools
from tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "You are AuraAgent, a personal AI assistant with access to a sandboxed "
    "Markdown note-taking system and a local calendar. Use the available "
    "tools to help the user manage their notes and schedule. Think step by "
    "step, and only call a tool when you need information or an action you "
    "can't provide from your own knowledge. Deleting or modifying an "
    "important calendar event may pause to ask the user for confirmation — "
    "if declined, treat it as a normal outcome and report it back plainly."
)


async def main() -> None:
    settings = load_settings()

    registry = ToolRegistry()
    register_notes_tools(registry, settings.notes_sandbox_root)

    calendar_provider = LocalJSONCalendarProvider(settings.calendar_events_file)
    confirmation_channel = TerminalConfirmationChannel()
    register_calendar_tools(registry, calendar_provider, confirmation_channel)
    # MCP / Skill registration land in a later iteration — see
    # mcp_integration/ and skills/ for their scaffolded seams.

    provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.model_id)
    logger = AuraLogger(settings.logs_dir)
    engine = AsyncReActEngine(
        provider=provider,
        registry=registry,
        logger=logger,
        system_prompt=SYSTEM_PROMPT,
        max_turns=settings.max_turns,
    )

    print("AuraAgent v1 — core ReAct loop + Markdown notes + local calendar. Type 'exit' to quit.\n")
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


if __name__ == "__main__":
    asyncio.run(main())
