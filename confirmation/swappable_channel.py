"""SwappableConfirmationChannel — a ConfirmationChannel that forwards every
call to whichever concrete channel it currently holds, swappable at runtime.
Same indirection pattern as providers/swappable_provider.py,
tools/workspace_root.py::SwappableWorkspaceRoot, and
tools/calendar/swappable_calendar_provider.py — this is the fourth
instance of it in this codebase, not a new pattern.

Every confirmation-gated tool (delete_file, kill_process, propose_*, the
calendar/task tools, ...) is registered in core/bootstrap.py against ONE
shared instance of this class instead of the real TerminalConfirmationChannel/
WebSocketConfirmationChannel directly. That single level of indirection is
what lets tools/scheduler/scheduler_loop.py temporarily swap in an
AutoDeclineConfirmationChannel for the duration of one unattended scheduled
run (see that module's docstring) and swap back afterward, without needing
to re-register any tool or touch the real human-facing channel at all.

Safe to swap mid-process specifically because tools/scheduler/ only ever
does so while holding AppContext.run_lock — the same lock every live
CLI/GUI turn also holds around orchestrator.run() — so there is never a
moment where a real human confirmation and an auto-declined scheduled one
could be in flight at the same time.
"""
from __future__ import annotations

from confirmation.base import ConfirmationChannel, ConfirmationRequest


class SwappableConfirmationChannel(ConfirmationChannel):
    def __init__(self, initial: ConfirmationChannel) -> None:
        self._current = initial

    @property
    def current(self) -> ConfirmationChannel:
        return self._current

    def set_current(self, new_channel: ConfirmationChannel) -> None:
        self._current = new_channel

    async def confirm(self, request: ConfirmationRequest) -> bool:
        return await self._current.confirm(request)

    async def ask_open_question(self, prompt: str) -> str:
        return await self._current.ask_open_question(prompt)
