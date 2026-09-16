"""remember_fact / recall_facts — cross-session memory tools.

Deliberately pull-based: facts are NOT auto-injected into every system
prompt turn (that would grow every single LLM call's token cost linearly
with how much has been remembered, working against the project's
token-cost-consciousness). Instead the LLM must actively call
recall_facts() when it judges a prior fact might be relevant — see the
SYSTEM_PROMPT guidance added in main.py.
"""
from __future__ import annotations

from typing import Any

from tools.base import ToolSpec
from tools.memory.memory_store import MemoryStore
from tools.registry import ToolRegistry


def register_memory_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    async def remember_fact(args: dict[str, Any]) -> str:
        fact = store.add_fact(args["content"])
        return f"Remembered (id={fact.id}): {fact.content}"

    async def recall_facts(args: dict[str, Any]) -> str:
        results = store.search_facts(args.get("query", ""))
        if not results:
            return "No matching facts found in memory."
        return "\n".join(f"- [{f.id}] {f.content} (remembered {f.created_at})" for f in results)

    registry.register(
        ToolSpec(
            name="remember_fact",
            description=(
                "Save a durable fact worth remembering across future conversations "
                "(e.g. a stated preference, decision, or standing detail). Do not use "
                "for transient task details already tracked in notes/tasks/calendar."
            ),
            input_schema={
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        ),
        remember_fact,
    )
    registry.register(
        ToolSpec(
            name="recall_facts",
            description=(
                "Search previously remembered facts by case-insensitive substring. "
                "Call this before assuming you don't know something the user may have "
                "told you in an earlier session. Leave query empty to list everything remembered."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": [],
            },
        ),
        recall_facts,
    )
