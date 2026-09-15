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

    def _write_jsonl(self, event_type: str, turn: int, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "event_type": event_type,
            "payload": payload,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_user_input(self, text: str) -> None:
        _console.print(f"\n[bold cyan][USER][/bold cyan] > {text}")
        self._write_jsonl("user_input", -1, {"text": text})

    def log_calling_llm(self, turn: int, model: str, tool_count: int) -> None:
        _console.print(f"[dim][TURN {turn}] Calling LLM (model={model}, tools={tool_count})...[/dim]")

    def log_thought(self, turn: int, thought: str | None) -> None:
        if not thought:
            return
        _console.print(Panel(thought, title=f"[TURN {turn}] THOUGHT", border_style="yellow"))
        self._write_jsonl("thought", turn, {"text": thought})

    def log_tool_call(self, turn: int, tool_name: str, arguments: dict[str, Any]) -> None:
        _console.print(
            Panel(
                json.dumps(arguments, ensure_ascii=False, indent=2),
                title=f"[TURN {turn}] TOOL CALL: {tool_name}",
                border_style="magenta",
            )
        )
        self._write_jsonl("tool_call", turn, {"tool_name": tool_name, "arguments": arguments})

    def log_observation(self, turn: int, tool_name: str, observation: str, is_error: bool) -> None:
        style = "red" if is_error else "green"
        title = f"[TURN {turn}] OBSERVATION ({tool_name})" + (" [ERROR]" if is_error else "")
        _console.print(Panel(observation, title=title, border_style=style))
        self._write_jsonl(
            "observation", turn, {"tool_name": tool_name, "content": observation, "is_error": is_error}
        )

    def log_confirmation(self, turn: int, prompt: str, decision: bool) -> None:
        _console.print(
            Panel(
                f"{prompt}\nProceed? [y/N]: {'y' if decision else 'N'}",
                title=f"[TURN {turn}] CONFIRMATION",
                border_style="red",
            )
        )
        self._write_jsonl("confirmation", turn, {"prompt": prompt, "decision": decision})

    def log_final_answer(self, text: str) -> None:
        _console.print(f"[bold green][AGENT][/bold green] {text}\n")
        self._write_jsonl("final_answer", -1, {"text": text})

    def log_error(self, turn: int, message: str) -> None:
        _console.print(f"[bold red][TURN {turn}] ERROR:[/bold red] {message}")
        self._write_jsonl("error", turn, {"message": message})
