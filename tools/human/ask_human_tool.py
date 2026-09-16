"""ask_human — lets the LLM pause and ask the human an open-ended
clarifying question mid-task, via the same ConfirmationChannel instance
already injected for calendar/task HITL confirmations (no second channel
constructed). Complements confirm()'s Y/N gating: this is for "I lack
information to proceed correctly," not "this action is risky."
"""
from __future__ import annotations

from typing import Any

from confirmation.base import ConfirmationChannel
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def register_ask_human_tools(registry: ToolRegistry, confirmation_channel: ConfirmationChannel) -> None:
    async def ask_human(args: dict[str, Any]) -> str:
        answer = await confirmation_channel.ask_open_question(args["question"])
        if not answer:
            return "Human provided no answer (empty response)."
        return f"Human answered: {answer}"

    registry.register(
        ToolSpec(
            name="ask_human",
            description=(
                "Pause and ask the human an open-ended clarifying question when you lack "
                "information needed to proceed safely or correctly. Use sparingly — only "
                "when a reasonable, clearly-stated assumption would not do."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The specific question to ask the human."}
                },
                "required": ["question"],
            },
        ),
        ask_human,
    )
