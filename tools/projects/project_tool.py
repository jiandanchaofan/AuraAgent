"""update_project_summary/remember_project_fact/recall_project_facts/
search_project_chats -- the LLM-facing side of the Project mechanism.
Everything about Projects THEMSELVES (creating one, entering/leaving one,
which directory is active, which Skill/MCP tools are enabled for it) is a
human-direct /project CLI command or GUI action (cli/commands.py,
gui/routes.py), not something the LLM initiates itself -- this mirrors
update_user_profile's own scope (the LLM records into an existing,
human-selected context, it doesn't create or switch contexts itself). All
four tools below only ever act on whichever project is CURRENTLY ACTIVE
(ActiveProjectState) -- there is deliberately no tool that lets the LLM
read or write a project other than the one the human is currently in.

register_project_chat_search_tool() is registered separately from
register_project_tools() (see its own docstring below) -- it needs a
ChatSessionStore, which is a GUI-only concept (constructed in
gui/server.py's create_app(), never passed into core/bootstrap.py's
build_app_context()) since the CLI has no notion of multiple named chats
per project to search across in the first place. Under the CLI,
search_project_chats is simply never registered into the shared
ToolRegistry -- config/agents.json can safely still list it in
orchestrator's capabilities, since ScopedToolRegistryView filtering a
pattern against a tool that doesn't exist in the underlying registry is
a normal, harmless no-op.
"""
from __future__ import annotations

from typing import Any, Callable

from core.react_engine import AsyncReActEngine
from tools.base import ToolSpec
from tools.projects.project_store import ActiveProjectState, ProjectStore, sync_leader_system_prompt
from tools.registry import ToolRegistry
from tools.sessions.session_store import ChatSessionStore


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
            role=args.get("role"),
            current_state=args.get("current_state"),
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
                "genuinely meaningful update, not on every turn. `role` and `current_state` each REPLACE "
                "the previous value wholesale (keep both short -- `role` is a persona/instructions block "
                "for how you should act in this project, e.g. 'Act as a compliance research assistant "
                "focused on Southeast Asia'; `current_state` is 2-4 sentences on where things stand -- "
                "this is what keeps the project's token cost bounded no matter how long it's been worked "
                "on, so do not just append to either). If you produced a notable file/deliverable, "
                "mention it briefly IN `current_state` (e.g. 'drafted report.pdf, now working on slides') "
                "-- there is no separate list for this; the real, authoritative, always-current catalog "
                "of files is the project's own directory (list_directory), not something to duplicate "
                "here. `open_questions` is a short one-line-entries list merged into the existing one "
                "(duplicates skipped, oldest dropped once it's long). The user can also edit "
                "`role`/`current_state`/`open_questions` directly themselves -- your update is a draft, "
                "not the only source of truth. If no project is active, this tool reports that clearly "
                "instead of erroring."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Short persona/instructions for how you should act in this project. Replaces the old one entirely.",
                    },
                    "current_state": {
                        "type": "string",
                        "description": (
                            "Short (2-4 sentence) summary of where this project stands now -- mention a notable "
                            "produced file in passing here if relevant. Replaces the old one entirely."
                        ),
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

    async def remember_project_fact(args: dict[str, Any]) -> str:
        if active.current_slug is None:
            return "No project is currently active -- ask the user to run /project use <slug> first."
        fact = await store.memory_store_for(active.current_slug).add_fact(args["content"])
        return f"Remembered (id={fact.id}): {fact.content}"

    registry.register(
        ToolSpec(
            name="remember_project_fact",
            description=(
                "Save a durable fact/detail into the CURRENTLY ACTIVE project's own memory -- separate "
                "from your global remember_fact, and separate from update_project_summary's "
                "current_state (which is for overall progress, not individual facts). NOT shown to you "
                "automatically -- call recall_project_facts to retrieve facts later. Use for project-"
                "specific knowledge worth keeping across sessions on this project (a decision made, a "
                "constraint discovered, a source cited), not one-off task details. NOT for files/"
                "deliverables you produced -- those live in the project's own directory (list_directory "
                "shows them accurately any time), mention a notable one in current_state instead of "
                "logging it here."
            ),
            input_schema={
                "type": "object",
                "properties": {"content": {"type": "string", "description": "The fact to remember, as a short standalone statement."}},
                "required": ["content"],
            },
        ),
        remember_project_fact,
    )

    async def recall_project_facts(args: dict[str, Any]) -> str:
        if active.current_slug is None:
            return "No project is currently active -- ask the user to run /project use <slug> first."
        facts = await store.memory_store_for(active.current_slug).search_facts(args.get("query", ""))
        if not facts:
            return "No matching facts found in this project's memory."
        return "\n".join(f"- (id={f.id}) {f.content}" for f in facts)

    registry.register(
        ToolSpec(
            name="recall_project_facts",
            description=(
                "Search the CURRENTLY ACTIVE project's own memory (facts saved via remember_project_fact). "
                "Call this when you need project-specific context that isn't already visible above and "
                "isn't in the current conversation -- not reflexively on every turn. Omit `query` to list "
                "all of this project's remembered facts."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Substring to search for. Omit to list all facts."}},
                "required": [],
            },
        ),
        recall_project_facts,
    )


def register_project_chat_search_tool(
    registry: ToolRegistry,
    active: ActiveProjectState,
    session_store: ChatSessionStore,
    current_session_id: Callable[[], str | None],
    leader_name: str,
    max_results: int = 20,
) -> None:
    """search_project_chats -- lets the Leader look something up in the
    CURRENTLY ACTIVE project's OTHER chats (never the one it's currently
    in, which is already part of its own conversation history). GUI-only
    -- see this module's own docstring for why. `current_session_id` is a
    callable (not a plain value) because gui/server.py's
    session_sink.active_session_id changes as the user switches chats;
    reading it fresh on every call, the same "never capture a mutable
    value at registration time" idiom as workspace_root.current, avoids
    this tool silently excluding the wrong session after a switch.
    """

    async def search_project_chats(args: dict[str, Any]) -> str:
        if active.current_slug is None:
            return "No project is currently active -- ask the user to run /project use <slug> first."
        query = args["query"].lower()
        exclude_id = current_session_id()
        matches: list[str] = []
        for session in await session_store.list_sessions(project_slug=active.current_slug):
            if session.id == exclude_id:
                continue
            for event in session_store.read_events(session.id):
                if event.get("agent_name") != leader_name:
                    continue
                if event.get("event_type") not in ("user_input", "final_answer"):
                    continue
                text = event.get("payload", {}).get("text", "")
                if query in text.lower():
                    snippet = text if len(text) <= 200 else f"{text[:200]}..."
                    matches.append(f"- [{session.title}] (id={session.id}): {snippet}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        if not matches:
            return f"No matches found in this project's other chats for '{args['query']}'."
        return "\n".join(matches)

    registry.register(
        ToolSpec(
            name="search_project_chats",
            description=(
                "Search the CURRENTLY ACTIVE project's OTHER chats (not this conversation) for a keyword "
                "or phrase -- use when the user references something discussed in a different chat on "
                "this same project ('did we cover X before?') that isn't already in the current "
                "conversation. Returns up to a handful of matching snippets with each chat's title. Call "
                "only when genuinely useful, not reflexively on every turn."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Keyword or phrase to search for."}},
                "required": ["query"],
            },
        ),
        search_project_chats,
    )
