"""update_user_profile — the only way the profile
(tools/profile/user_profile_store.py) changes; there is deliberately no
matching "read" tool (unlike remember_fact/recall_facts), because the
profile is meant to be always visible via the system prompt, not queried
on demand — see user_profile_store.py's module docstring for why a
mid-session update doesn't retroactively change the CURRENT run's system
prompt.
"""
from __future__ import annotations

from typing import Any

from tools.base import ToolSpec
from tools.profile.user_profile_store import UserProfileStore
from tools.registry import ToolRegistry


def register_user_profile_tools(registry: ToolRegistry, store: UserProfileStore) -> None:
    async def update_user_profile(args: dict[str, Any]) -> str:
        profile = await store.update_profile(
            preferences=args.get("preferences"),
            habits=args.get("habits"),
            common_topics=args.get("common_topics"),
            notes=args.get("notes"),
        )
        return f"Profile updated. Current profile:\n{profile.render() or '(empty)'}"

    registry.register(
        ToolSpec(
            name="update_user_profile",
            description=(
                "Record something durable about the USER THEMSELVES (not the current task) into "
                "their profile: a stated preference, a habit, or a topic they come back to "
                "repeatedly. This is shown to you automatically in every future session's system "
                "prompt -- you never need to look it up. Call it sparingly, only when you notice "
                "something genuinely durable and reusable; do not call it for one-off task details "
                "or on every turn. Any field you omit is left unchanged; list fields are merged "
                "(duplicates are skipped, nothing is ever removed), notes are appended rather than "
                "replaced."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "preferences": {"type": "array", "items": {"type": "string"}},
                    "habits": {"type": "array", "items": {"type": "string"}},
                    "common_topics": {"type": "array", "items": {"type": "string"}},
                    "notes": {
                        "type": "string",
                        "description": "Free-text note, appended to any existing notes.",
                    },
                },
                "required": [],
            },
        ),
        update_user_profile,
    )
