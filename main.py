"""AuraAgent CLI entrypoint — the terminal frontend.

Everything that used to be composed directly in this file (ToolRegistry,
AgentRegistry, every Agent's engine, all six self-extension tools) now
lives in core/bootstrap.py::build_app_context(), shared with the GUI
backend (gui/server.py). This file's only job is the CLI-specific half:
build a TerminalConfirmationChannel + terminal log sinks, call
build_app_context(), then run the REPL loop.
"""
from __future__ import annotations

import asyncio

from cli.commands import dispatch_command, is_command
from config.settings import load_settings
from confirmation.terminal_channel import TerminalConfirmationChannel
from core.bootstrap import build_app_context
from core.logger import AuraLogger, JSONLSink, TerminalSink, print_banner
from core.message_types import ConversationTurn


async def main() -> None:
    settings = load_settings()
    logger = AuraLogger([TerminalSink(), JSONLSink(settings.logs_dir)])
    confirmation_channel = TerminalConfirmationChannel(logger)

    ctx = await build_app_context(settings, confirmation_channel, logger)
    # Persists for the whole process lifetime -- every line typed at the
    # REPL shares this one history, so the orchestrator actually remembers
    # earlier turns (Epic N1). Restart the process for a clean slate; no
    # in-REPL "new chat" command yet (deliberately out of scope for N1).
    history: list[ConversationTurn] = []

    print_banner(
        f"AuraAgent, developed by James Jiang | provider={settings.llm_provider} model={settings.model_id} | "
        f"workspace={ctx.workspace_root.current} | notes={ctx.notes_root.current} | type 'exit' to quit"
    )
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
                    await dispatch_command(user_input, ctx.cli_context)
                except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
                    print(f"[ERROR] {type(exc).__name__}: {exc}")
                continue
            try:
                # Shared with tools/scheduler/scheduler_loop.py's own
                # runs -- guarantees a background scheduled task never
                # fires mid-turn and fights over workspace_root/
                # active_project with what's being typed here right now.
                async with ctx.run_lock:
                    await ctx.orchestrator.run(user_input, history=history)
            except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
                print(f"[ERROR] {type(exc).__name__}: {exc}")
    finally:
        await ctx.aclose()


if __name__ == "__main__":
    asyncio.run(main())
