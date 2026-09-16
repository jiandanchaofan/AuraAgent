"""ConfirmationChannel — the Human-in-the-loop abstraction.

core/react_engine.py never imports this module. Confirmation is entirely
encapsulated inside whichever tool handler needs it (the calendar tool,
wired up in a later iteration) — the engine's job stays exactly "loop /
call the provider / dispatch tools / log", nothing about HITL leaks into
it. That is what lets a future WebConfirmationChannel (awaiting an
approval resolved via a FastAPI endpoint) replace TerminalConfirmationChannel
without touching core/ or tools/registry.py at all.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ConfirmationRequest:
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    risk_level: str = "destructive"  # "destructive" | "important_modification"


class ConfirmationChannel(ABC):
    @abstractmethod
    async def confirm(self, request: ConfirmationRequest) -> bool:
        """Return True to proceed, False to abort the tool call."""
        ...

    @abstractmethod
    async def ask_open_question(self, prompt: str) -> str:
        """Pause and ask the human a free-text clarifying question, returning
        their answer verbatim (empty string if they gave none). This is the
        same physical channel as confirm() — one object represents "pause
        and talk to the human" — just a different response shape (free text
        instead of yes/no), which is why this lives on the same ABC rather
        than a parallel interface."""
        ...
