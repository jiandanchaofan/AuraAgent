"""Terminal Y/N implementation of ConfirmationChannel.

Runs the blocking input() call in a worker thread via asyncio.to_thread so
it never blocks the event loop — this is what a future WebConfirmationChannel
will replace once AuraAgent grows a FastAPI backend, without any change to
whatever tool code calls ConfirmationChannel.confirm().

Not yet wired into any tool — lands together with the calendar tool in the
next iteration (see tools/calendar/calendar_tool.py).
"""
from __future__ import annotations

import asyncio

from confirmation.base import ConfirmationChannel, ConfirmationRequest


class TerminalConfirmationChannel(ConfirmationChannel):
    async def confirm(self, request: ConfirmationRequest) -> bool:
        def _prompt() -> bool:
            print(f"\n[CONFIRMATION REQUIRED] {request.reason}")
            answer = input("Proceed? [y/N]: ").strip().lower()
            return answer == "y"

        return await asyncio.to_thread(_prompt)

    async def ask_open_question(self, prompt: str) -> str:
        def _prompt() -> str:
            return input(f"\n[QUESTION] {prompt}\n> ").strip()

        return await asyncio.to_thread(_prompt)
