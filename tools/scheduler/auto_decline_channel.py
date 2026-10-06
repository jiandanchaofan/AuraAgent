"""AutoDeclineConfirmationChannel -- the ConfirmationChannel a scheduled,
unattended run (scheduler_loop.py) is temporarily switched to via
SwappableConfirmationChannel for the duration of its execution.

No human is present to answer a confirmation during an unattended run, so
this fails safe: every confirm() is declined, every ask_open_question()
comes back empty -- exactly what a tool handler already treats as a
normal "user said no" / "user gave no answer" outcome, not a crash. Every
decision is still logged via AuraLogger.log_confirmation() (the same call
TerminalConfirmationChannel/WebSocketConfirmationChannel make), so a
declined step is visible in that run's own JSONL trace afterward, not
silently swallowed.
"""
from __future__ import annotations

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.logger import AuraLogger


class AutoDeclineConfirmationChannel(ConfirmationChannel):
    def __init__(self, logger: AuraLogger) -> None:
        self._logger = logger

    async def confirm(self, request: ConfirmationRequest) -> bool:
        self._logger.log_confirmation(-1, f"[unattended scheduled run, auto-declined] {request.reason}", False)
        return False

    async def ask_open_question(self, prompt: str) -> str:
        return ""
