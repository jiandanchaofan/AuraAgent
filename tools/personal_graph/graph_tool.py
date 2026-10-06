"""Leader-facing tools over the Auralis personal data graph
(tools/personal_graph/graph_store.py): three read-only queries plus
create_footprint, the chat-typed "record a footprint" entry point.

create_footprint is deliberately independent from
tools/notes/thino_format.py's save_quick_note (gui/server.py /
tools/notes/notes_tool.py) -- two separate datasets, never auto-merged
(explicit user decision): save_quick_note writes a Thino-style Markdown
bullet into a daily note file for quick capture; create_footprint writes
a structured row into the personal graph, the same model Auralis itself
pushes into. Don't wire one into the other.

create_footprint never guesses a `type` the caller didn't give it --
that kind of inference (classifying an under-specified footprint,
auto-linking based on loose text matching) is deliberately left for a
future dedicated Worker triggered by sync (source="ai"), not this tool.
When the caller DOES name a person/project explicitly, the resulting
link is source="user" (a human-directed instruction relayed through the
Leader, not an inference) -- see graph_store.py's own human-authority
docstring for why that distinction matters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from uuid6 import uuid7

from tools.base import ToolSpec
from tools.personal_graph.graph_links import resolve_footprint_mentions
from tools.personal_graph.graph_store import GraphStore
from tools.registry import ToolRegistry


def register_personal_graph_tools(registry: ToolRegistry, graph_store: GraphStore) -> None:
    async def recall_person(args: dict[str, Any]) -> str:
        matches = await graph_store.find_person(args["name_or_alias"])
        if not matches:
            return f"No person found matching '{args['name_or_alias']}'."
        lines = []
        for p in matches:
            extra = f" (aliases: {', '.join(p['aliases'])})" if p.get("aliases") else ""
            lines.append(f"{p['id']}: {p['name']}{extra}")
        return "\n".join(lines)

    async def recall_footprints(args: dict[str, Any]) -> str:
        person_id = None
        if args.get("person"):
            matches = await graph_store.find_person(args["person"])
            if not matches:
                return f"No person found matching '{args['person']}' -- nothing to show."
            person_id = matches[0]["id"]
        footprints = await graph_store.list_footprints(
            person_id=person_id,
            project_tag=args.get("project_tag"),
            keyword=args.get("keyword"),
            since=args.get("since"),
        )
        if not footprints:
            return "No matching footprints found."
        return "\n".join(f"[{f['occurred_at']}] {f['text']}" for f in footprints)

    async def recall_project_context(args: dict[str, Any]) -> str:
        tag = args["tag"]
        project = await graph_store.get_project(tag)
        if project is None:
            return f"No project found with tag '{tag}'."
        footprints = await graph_store.list_footprints(project_tag=tag, limit=10)
        lines = [f"Project '{project['name'] or tag}' (#{tag}): {project.get('goal') or '(no goal set)'}"]
        if footprints:
            lines.append("Recent footprints:")
            lines.extend(f"- [{f['occurred_at']}] {f['text']}" for f in footprints)
        return "\n".join(lines)

    async def create_footprint(args: dict[str, Any]) -> str:
        footprint_id = str(uuid7())
        patch: dict[str, Any] = {
            "text": args["text"],
            "occurred_at": args.get("occurred_at") or datetime.now(timezone.utc).isoformat(),
        }
        if args.get("location"):
            patch["location"] = args["location"]
        # No type given -> leave it at "default" priority (see graph_store.py's
        # _TYPE_SOURCE_PRIORITY), open for a future Worker to classify. A type
        # the caller DID give is "manual" -- a human instruction relayed
        # through the Leader, not this tool inferring one on its own.
        if args.get("type"):
            patch["type"] = args["type"]
            patch["type_source"] = "manual"
        else:
            patch["type_source"] = "default"
        await graph_store.apply_op(str(uuid4()), "footprint", footprint_id, "upsert", patch, source="user")

        linked = await resolve_footprint_mentions(
            graph_store, footprint_id, args.get("person_names"), args.get("project_tag"), source="user"
        )

        suffix = f" (linked to {', '.join(linked)})" if linked else ""
        return f"Saved footprint '{footprint_id}'{suffix}."

    registry.register(
        ToolSpec(
            name="recall_person",
            description="Look up a person in the personal footprint/relationship graph (synced from Auralis) by name or alias.",
            input_schema={
                "type": "object",
                "properties": {"name_or_alias": {"type": "string", "description": "Name or alias substring to search for"}},
                "required": ["name_or_alias"],
            },
        ),
        recall_person,
    )
    registry.register(
        ToolSpec(
            name="recall_footprints",
            description=(
                "Search footprints (life-event atoms synced from Auralis) by person, project tag, keyword, or date -- "
                "any combination, all optional."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "Only footprints linked to this person (matched by name/alias)"},
                    "project_tag": {"type": "string", "description": "Only footprints linked to this project's #tag"},
                    "keyword": {"type": "string", "description": "Substring to match in the footprint's text"},
                    "since": {"type": "string", "description": "ISO date/datetime -- only footprints occurring on or after this"},
                },
                "required": [],
            },
        ),
        recall_footprints,
    )
    registry.register(
        ToolSpec(
            name="recall_project_context",
            description="Get a project's (synced from Auralis) goal plus its most recent linked footprints, by its #tag.",
            input_schema={
                "type": "object",
                "properties": {"tag": {"type": "string", "description": "The project's canonical #tag"}},
                "required": ["tag"],
            },
        ),
        recall_project_context,
    )
    registry.register(
        ToolSpec(
            name="create_footprint",
            description=(
                "Record a new footprint (a life-event atom) into the personal footprint/relationship graph shared with "
                "Auralis -- use this when the user explicitly asks to record/save a footprint about something that "
                "happened, NOT for a quick, fragmented note (use save_quick_note for that instead -- these are two "
                "separate things). Optionally link it to people and/or a project the user names explicitly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The footprint's text, verbatim"},
                    "occurred_at": {"type": "string", "description": "ISO datetime it happened -- defaults to now"},
                    "location": {"type": "string", "description": "Optional location"},
                    "type": {
                        "type": "string",
                        "enum": ["interpersonal", "idea", "project", "excerpt"],
                        "description": "Only set this if the user explicitly said what kind it is -- omit it otherwise, don't guess",
                    },
                    "person_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of people explicitly mentioned as involved, if any",
                    },
                    "project_tag": {"type": "string", "description": "A project's #tag this footprint explicitly belongs to, if any"},
                },
                "required": ["text"],
            },
        ),
        create_footprint,
    )
