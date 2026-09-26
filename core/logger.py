"""White-box execution logger.

Every Thought / Tool Call / Observation event the ReAct engine produces is
built into one small "event" dict (ts/turn/agent_name/event_type/payload)
and handed to every registered `LogSink`. This is the seam a GUI backend
plugs into: `AuraLogger` itself has no idea whether it's talking to a
terminal, a JSONL file, or a live WebSocket connection — it just builds
events and fans them out. CLI mode uses `[TerminalSink(), JSONLSink(dir)]`
(unchanged terminal output, still also durably logged); a future GUI
backend uses `[JSONLSink(dir), WebSocketSink(connection)]` instead of
TerminalSink, since the browser window IS the "terminal" there.

AuraLogger.log_*() methods are always synchronous, and LogSink.write()
must never block or await — core/react_engine.py calls these methods
directly, with no `await`, so a sink needing to do real async I/O (like
pushing over an open WebSocket) must buffer internally (e.g. an
asyncio.Queue via put_nowait()) and drain it from its own task, rather
than making write() itself a coroutine. This keeps the engine completely
unaware that a GUI exists, the same way it stays unaware of Multi-Agent,
MCP, or self-extension.
"""
from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyfiglet
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

# LLM output routinely contains emoji / non-BMP characters. On Windows,
# `sys.stdout` may default to the system codepage (e.g. GBK), and rich's
# "legacy Windows terminal" render path writes through the Win32 console
# API using that codepage directly rather than respecting stdout's own
# encoding — so a stray emoji can crash the whole REPL. Reconfiguring
# stdout/stderr to UTF-8 and forcing rich off the legacy code path (ANSI
# escapes instead) avoids both failure modes; `legacy_windows=False` is
# safe on any terminal that understands ANSI, which includes Windows 10+.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def print_banner(subtitle: str = "") -> None:
    """Prints a big ASCII-art "AuraAgent" splash banner at startup.
    Purely cosmetic — main.py calls this once before the REPL loop starts."""
    console = Console(legacy_windows=False)
    banner = pyfiglet.figlet_format("AuraAgent", font="ansi_shadow")
    console.print(banner, style="bold cyan", highlight=False)
    if subtitle:
        console.print(subtitle, style="dim", highlight=False)
    console.print()


class LogSink(ABC):
    """One destination for white-box events. See the module docstring for
    why write() must never await/block."""

    @abstractmethod
    def write(self, event: dict[str, Any]) -> None: ...


class TerminalSink(LogSink):
    """Renders each event as a colored `rich` panel — this is exactly the
    rendering AuraLogger used to do inline, before sinks existed, just
    moved into its own class with one `_render_<event_type>` method per
    event type instead of one `log_<event_type>` method doing both the
    rendering AND the (now removed) direct JSONL write."""

    def __init__(self) -> None:
        self._console = Console(legacy_windows=False)

    def write(self, event: dict[str, Any]) -> None:
        renderer = getattr(self, f"_render_{event['event_type']}", None)
        if renderer is not None:
            renderer(event["agent_name"], event["turn"], event["payload"])

    def _render_user_input(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        label = escape(f"[{agent_name}] [USER]")
        self._console.print(f"\n[bold cyan]{label}[/bold cyan] > {escape(payload['text'])}")

    def _render_calling_llm(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        label = escape(
            f"[{agent_name}] [TURN {turn}] Calling LLM (model={payload['model']}, tools={payload['tool_count']})..."
        )
        self._console.print(f"[dim]{label}[/dim]")

    def _render_thought(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        title = escape(f"[{agent_name}] [TURN {turn}] THOUGHT")
        self._console.print(Panel(escape(payload["text"]), title=title, border_style="yellow"))

    def _render_tool_call(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        # call_id is included in the title (not just the JSONL payload)
        # because concurrent tool dispatch (see core/react_engine.py) means
        # multiple TOOL CALL / OBSERVATION panels can interleave in the
        # terminal — the id is what lets a human re-pair them visually.
        call_id = payload.get("call_id", "")
        id_suffix = f" [{call_id[-6:]}]" if call_id else ""
        title = escape(f"[{agent_name}] [TURN {turn}] TOOL CALL: {payload['tool_name']}{id_suffix}")
        self._console.print(
            Panel(
                escape(json.dumps(payload["arguments"], ensure_ascii=False, indent=2)),
                title=title,
                border_style="magenta",
            )
        )

    def _render_observation(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        is_error = payload["is_error"]
        style = "red" if is_error else "green"
        call_id = payload.get("call_id", "")
        id_suffix = f" [{call_id[-6:]}]" if call_id else ""
        title = escape(
            f"[{agent_name}] [TURN {turn}] OBSERVATION ({payload['tool_name']}){id_suffix}"
            + (" [ERROR]" if is_error else "")
        )
        self._console.print(Panel(escape(payload["content"]), title=title, border_style=style))

    def _render_confirmation(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        title = escape(f"[{agent_name}] [TURN {turn}] CONFIRMATION")
        body = escape(f"{payload['prompt']}\nProceed? [y/N]: {'y' if payload['decision'] else 'N'}")
        self._console.print(Panel(body, title=title, border_style="red"))

    def _render_final_answer(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        label = escape(f"[{agent_name}] [AGENT]")
        self._console.print(f"[bold green]{label}[/bold green] {escape(payload['text'])}\n")

    def _render_error(self, agent_name: str, turn: int, payload: dict[str, Any]) -> None:
        label = escape(f"[{agent_name}] [TURN {turn}] ERROR:")
        self._console.print(f"[bold red]{label}[/bold red] {escape(payload['message'])}")


class JSONLSink(LogSink):
    """Appends every event as one structured JSON line to
    logs/session-<timestamp>.jsonl — the durable audit trail, independent
    of whatever else is watching (terminal, GUI, both, or neither)."""

    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._log_path = log_dir / f"session-{timestamp}.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        # No lock needed here: the whole write is synchronous with no
        # `await` inside, so under asyncio's cooperative scheduling this is
        # already an atomic critical section — concurrent agents can only
        # ever produce nondeterministic *line order*, never a torn line.
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


class AuraLogger:
    def __init__(self, sinks: list[LogSink]) -> None:
        self._sinks = sinks

    def _emit(self, event_type: str, turn: int, payload: dict[str, Any], agent_name: str) -> None:
        # agent_name is a top-level field (sibling of turn/event_type), not
        # buried in payload, so a consumer can filter/group a multi-agent
        # trace by agent without knowing payload internals.
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "agent_name": agent_name,
            "event_type": event_type,
            "payload": payload,
        }
        for sink in self._sinks:
            sink.write(event)

    def log_user_input(self, text: str, agent_name: str = "root") -> None:
        self._emit("user_input", -1, {"text": text}, agent_name)

    def log_calling_llm(self, turn: int, model: str, tool_count: int, agent_name: str = "root") -> None:
        self._emit("calling_llm", turn, {"model": model, "tool_count": tool_count}, agent_name)

    def log_thought(self, turn: int, thought: str | None, agent_name: str = "root") -> None:
        if not thought:
            return
        self._emit("thought", turn, {"text": thought}, agent_name)

    def log_tool_call(
        self, turn: int, tool_name: str, arguments: dict[str, Any], agent_name: str = "root", call_id: str = ""
    ) -> None:
        self._emit(
            "tool_call", turn, {"tool_name": tool_name, "arguments": arguments, "call_id": call_id}, agent_name
        )

    def log_observation(
        self,
        turn: int,
        tool_name: str,
        observation: str,
        is_error: bool,
        agent_name: str = "root",
        call_id: str = "",
    ) -> None:
        self._emit(
            "observation",
            turn,
            {"tool_name": tool_name, "content": observation, "is_error": is_error, "call_id": call_id},
            agent_name,
        )

    def log_confirmation(self, turn: int, prompt: str, decision: bool, agent_name: str = "root") -> None:
        self._emit("confirmation", turn, {"prompt": prompt, "decision": decision}, agent_name)

    def log_final_answer(self, text: str, agent_name: str = "root") -> None:
        self._emit("final_answer", -1, {"text": text}, agent_name)

    def log_error(self, turn: int, message: str, agent_name: str = "root") -> None:
        self._emit("error", turn, {"message": message}, agent_name)
