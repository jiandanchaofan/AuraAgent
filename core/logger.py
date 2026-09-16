"""White-box execution logger.

Every Thought / Tool Call / Observation event the ReAct engine produces is
printed to the terminal (via `rich` panels) AND appended as a structured
JSON line to logs/session-<timestamp>.jsonl. The JSONL trail is the
durable audit log a future FastAPI backend could stream over a websocket
instead of printing — the two sinks are independent by design.
"""
from __future__ import annotations

import json
import sys
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

_console = Console(legacy_windows=False)


def print_banner(subtitle: str = "") -> None:
    """Prints a big ASCII-art "AuraAgent" splash banner at startup.
    Purely cosmetic — main.py calls this once before the REPL loop starts."""
    banner = pyfiglet.figlet_format("AuraAgent", font="ansi_shadow")
    _console.print(banner, style="bold cyan", highlight=False)
    if subtitle:
        _console.print(subtitle, style="dim", highlight=False)
    _console.print()


class AuraLogger:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._log_path = log_dir / f"session-{timestamp}.jsonl"

    def _write_jsonl(self, event_type: str, turn: int, payload: dict[str, Any], agent_name: str = "root") -> None:
        # agent_name is a top-level field (sibling of turn/event_type), not
        # buried in payload, so a future consumer can filter/group a
        # multi-agent trace by agent without knowing payload internals.
        # No lock needed here: the whole write is synchronous with no
        # `await` inside, so under asyncio's cooperative scheduling this is
        # already an atomic critical section — concurrent agents can only
        # ever produce nondeterministic *line order*, never a torn line.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "agent_name": agent_name,
            "event_type": event_type,
            "payload": payload,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_user_input(self, text: str, agent_name: str = "root") -> None:
        label = escape(f"[{agent_name}] [USER]")
        _console.print(f"\n[bold cyan]{label}[/bold cyan] > {escape(text)}")
        self._write_jsonl("user_input", -1, {"text": text}, agent_name=agent_name)

    def log_calling_llm(self, turn: int, model: str, tool_count: int, agent_name: str = "root") -> None:
        label = escape(f"[{agent_name}] [TURN {turn}] Calling LLM (model={model}, tools={tool_count})...")
        _console.print(f"[dim]{label}[/dim]")

    def log_thought(self, turn: int, thought: str | None, agent_name: str = "root") -> None:
        if not thought:
            return
        title = escape(f"[{agent_name}] [TURN {turn}] THOUGHT")
        _console.print(Panel(escape(thought), title=title, border_style="yellow"))
        self._write_jsonl("thought", turn, {"text": thought}, agent_name=agent_name)

    def log_tool_call(
        self, turn: int, tool_name: str, arguments: dict[str, Any], agent_name: str = "root", call_id: str = ""
    ) -> None:
        # call_id is included in the title (not just the JSONL payload)
        # because concurrent tool dispatch (see core/react_engine.py) means
        # multiple TOOL CALL / OBSERVATION panels can interleave in the
        # terminal — the id is what lets a human re-pair them visually.
        #
        # Every dynamic string here goes through rich.markup.escape() before
        # reaching Panel(): Rich's Console treats plain strings as markup by
        # default, so unescaped `[...]` in a tool name, an id, or — worst of
        # all — the JSON-serialized arguments/observation body (which
        # routinely contains literal `[` `]` for JSON arrays) gets silently
        # swallowed rather than displayed. This was a real, reproducible bug
        # (e.g. list_tasks/list_calendar_events/recall_facts observations
        # use a "- [id] ..." format that rich was eating), not just a
        # precaution for the new agent_name prefix.
        id_suffix = f" [{call_id[-6:]}]" if call_id else ""
        title = escape(f"[{agent_name}] [TURN {turn}] TOOL CALL: {tool_name}{id_suffix}")
        _console.print(
            Panel(
                escape(json.dumps(arguments, ensure_ascii=False, indent=2)),
                title=title,
                border_style="magenta",
            )
        )
        self._write_jsonl(
            "tool_call",
            turn,
            {"tool_name": tool_name, "arguments": arguments, "call_id": call_id},
            agent_name=agent_name,
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
        style = "red" if is_error else "green"
        id_suffix = f" [{call_id[-6:]}]" if call_id else ""
        title = escape(
            f"[{agent_name}] [TURN {turn}] OBSERVATION ({tool_name}){id_suffix}"
            + (" [ERROR]" if is_error else "")
        )
        _console.print(Panel(escape(observation), title=title, border_style=style))
        self._write_jsonl(
            "observation",
            turn,
            {"tool_name": tool_name, "content": observation, "is_error": is_error, "call_id": call_id},
            agent_name=agent_name,
        )

    def log_confirmation(self, turn: int, prompt: str, decision: bool, agent_name: str = "root") -> None:
        title = escape(f"[{agent_name}] [TURN {turn}] CONFIRMATION")
        body = escape(f"{prompt}\nProceed? [y/N]: {'y' if decision else 'N'}")
        _console.print(Panel(body, title=title, border_style="red"))
        self._write_jsonl("confirmation", turn, {"prompt": prompt, "decision": decision}, agent_name=agent_name)

    def log_final_answer(self, text: str, agent_name: str = "root") -> None:
        label = escape(f"[{agent_name}] [AGENT]")
        _console.print(f"[bold green]{label}[/bold green] {escape(text)}\n")
        self._write_jsonl("final_answer", -1, {"text": text}, agent_name=agent_name)

    def log_error(self, turn: int, message: str, agent_name: str = "root") -> None:
        label = escape(f"[{agent_name}] [TURN {turn}] ERROR:")
        _console.print(f"[bold red]{label}[/bold red] {escape(message)}")
        self._write_jsonl("error", turn, {"message": message}, agent_name=agent_name)
