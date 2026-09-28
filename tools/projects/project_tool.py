"""update_project_summary — the only tool the LLM has for the Project
mechanism. Everything else about Projects (creating one, entering/leaving
one, which directory is active) is a human-direct /project CLI command
(cli/commands.py), not something the LLM initiates itself -- this mirrors
update_user_profile's own scope (the LLM records into an existing,
human-selected context, it doesn't create or switch contexts itself).
"""
from __future__ import annotations

from typing import Any

from core.react_engine import AsyncReActEngine
from tools.base import ToolSpec
from tools.projects.project_store import ActiveProjectState, ProjectStore, sync_leader_system_prompt
from tools.registry import ToolRegistry


def register_project_tools(
    registry: ToolRegistry,
    store: ProjectStore,
    active: ActiveProjectState,
    leader_engine: AsyncReActEngine,
    base_leader_system_prompt: str,
) -> None:
    async def update_project_summary(args: dict[str, Any]) -> str:
        if active.current_slug is None:
            return "No project is currently active -- ask the user to run /project use <slug> first."
        summary = await store.update_summary(
            active.current_slug,
            current_state=args.get("current_state"),
            outputs=args.get("outputs"),
            open_questions=args.get("open_questions"),
        )
        # Takes effect starting the very next turn (same session, no
        # restart) -- see sync_leader_system_prompt's own docstring.
        await sync_leader_system_prompt(leader_engine, base_leader_system_prompt, store, active)
        return f"Project summary updated.\n{summary.render() or '(empty)'}"

    registry.register(
        ToolSpec(
            name="update_project_summary",
            description=(
                "Record durable progress into the CURRENTLY ACTIVE project's summary (see /project in "
                "the CLI for how a project is selected). Call this sparingly -- only when there is a "
                "genuinely meaningful update, not on every turn. `current_state` REPLACES the previous "
                "state wholesale (keep it short, 2-4 sentences summarizing where things stand -- this is "
                "what keeps the project's token cost bounded no matter how long it's been worked on, so "
                "do not just append to it). `outputs`/`open_questions` are short one-line entries merged "
                "into existing lists (duplicates skipped, oldest dropped once the list is long). If no "
                "project is active, this tool reports that clearly instead of erroring."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "current_state": {
                        "type": "string",
                        "description": "Short (2-4 sentence) summary of where this project stands now. Replaces the old one entirely.",
                    },
                    "outputs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New '<file/deliverable> -- one-line note' entries produced this session.",
                    },
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New unresolved questions or next steps worth remembering.",
                    },
                },
                "required": [],
            },
        ),
        update_project_summary,
    )
