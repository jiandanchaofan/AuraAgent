"""Shared "match an existing person/project by name, or create a minimal
stub when unmatched, then link it to a footprint" logic -- extracted from
tools/personal_graph/graph_tool.py::create_footprint so the same behavior
is available to the human-facing REST quick-entry route (gui/graph_routes.py,
source="user") and the async AI-enrichment background task (source="ai"),
without duplicating it. graph_store.py itself stays pure storage; this
module is the one place that orchestrates multiple apply_op calls to
resolve a mention into a link.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from uuid6 import uuid7

from tools.personal_graph.graph_store import GraphStore


async def match_or_create_person(graph_store: GraphStore, name: str, *, source: str = "user") -> dict[str, Any]:
    matches = await graph_store.find_person(name)
    if matches:
        return matches[0]
    person_id = str(uuid7())
    await graph_store.apply_op(str(uuid4()), "person", person_id, "upsert", {"name": name}, source=source)
    return {"id": person_id, "name": name}


async def match_or_create_project(graph_store: GraphStore, tag: str, *, source: str = "user") -> dict[str, Any]:
    project = await graph_store.get_project(tag)
    if project is not None:
        return project
    project_id = str(uuid7())
    await graph_store.apply_op(str(uuid4()), "project", project_id, "upsert", {"tag": tag}, source=source)
    return {"id": project_id, "tag": tag}


async def link_footprint_to_person(
    graph_store: GraphStore, footprint_id: str, person_id: str, *, source: str = "user"
) -> dict[str, Any]:
    return await graph_store.apply_op(
        str(uuid4()),
        "link",
        str(uuid7()),
        "upsert",
        {"from_entity": "footprint", "from_id": footprint_id, "to_entity": "person", "to_id": person_id, "source": source},
        source=source,
    )


async def link_footprint_to_project(
    graph_store: GraphStore, footprint_id: str, project_id: str, *, source: str = "user"
) -> dict[str, Any]:
    return await graph_store.apply_op(
        str(uuid4()),
        "link",
        str(uuid7()),
        "upsert",
        {"from_entity": "footprint", "from_id": footprint_id, "to_entity": "project", "to_id": project_id, "source": source},
        source=source,
    )


async def resolve_footprint_mentions(
    graph_store: GraphStore,
    footprint_id: str,
    person_names: list[str] | None,
    project_tag: str | None,
    *,
    source: str = "user",
) -> list[str]:
    """Matches/creates+links each person name and the project tag (if any)
    to `footprint_id`, returning human-readable labels (e.g.
    ["Xiaoming", "#fitness"]) for a confirmation message. `source` is
    "user" for an explicit, human-directed mention (typed @/# or named to
    the Leader) and "ai" for one the async enrichment pass inferred --
    apply_op's own human-authority guard (a link rejected if a user or
    dismissed one already exists at the same edge) applies automatically
    either way, since both paths go through apply_op."""
    linked: list[str] = []
    for person_name in person_names or []:
        person = await match_or_create_person(graph_store, person_name, source=source)
        await link_footprint_to_person(graph_store, footprint_id, person["id"], source=source)
        linked.append(person_name)

    if project_tag:
        project = await match_or_create_project(graph_store, project_tag, source=source)
        await link_footprint_to_project(graph_store, footprint_id, project["id"], source=source)
        linked.append(f"#{project_tag}")

    return linked
