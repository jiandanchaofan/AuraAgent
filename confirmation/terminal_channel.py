"""Terminal Y/N implementation of ConfirmationChannel.

Runs the blocking input() call in a worker thread via asyncio.to_thread so
it never blocks the event loop — this is what a future WebConfirmationChannel
will replace once AuraAgent grows a FastAPI backend, without any change to
whatever tool code calls ConfirmationChannel.confirm().
"""
from __future__ import annotations

import asyncio

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.logger import AuraLogger


class TerminalConfirmationChannel(ConfirmationChannel):
    def __init__(self, logger: AuraLogger) -> None:
        self._logger = logger

    async def confirm(self, request: ConfirmationRequest) -> bool:
        def _prompt() -> bool:
            print(f"\n[CONFIRMATION REQUIRED] {request.reason}")
            answer = input("Proceed? [y/N]: ").strip().lower()
            return answer == "y"

        decision = await asyncio.to_thread(_prompt)
        # turn/agent_name aren't part of ConfirmationRequest today (the
        # tool handlers that build one don't know their own turn number),
        # so this lands as a "root"/untimed event, same convention
        # log_user_input/log_final_answer already use for turn=-1. Good
        # enough to get confirmations into the white-box trail at all —
        # they were completely invisible to JSONL/GUI before this.
        self._logger.log_confirmation(-1, request.reason, decision)
        return decision

    async def ask_open_question(self, prompt: str) -> str:
        # Deliberately NOT logged: this is the same channel
        # propose_mcp_server uses to collect env-var *values* (secrets)
        # from a human — see tools/self_extend/propose_mcp_tool.py. Those
        # must never reach logs/session-*.jsonl or a GUI event stream, so
        # this method has no log_* call at all, not even for the prompt text.
        def _prompt() -> str:
            return input(f"\n[QUESTION] {prompt}\n> ").strip()

        return await asyncio.to_thread(_prompt)
