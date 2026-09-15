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
