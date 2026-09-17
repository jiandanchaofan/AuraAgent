"""Local Markdown note management tools: search_notes, read_note,
create_note, update_note. All filesystem access is confined to the notes
sandbox root via tools/sandbox_path.resolve_within_sandbox().
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox


def register_notes_tools(registry: ToolRegistry, sandbox_root: Path) -> None:
    """Registers the four note tools against `sandbox_root`, creating it if needed."""
    sandbox_root.mkdir(parents=True, exist_ok=True)

    async def search_notes(args: dict[str, Any]) -> str:
        keyword = args["keyword"]
        matches: list[str] = []
        for md_file in sorted(sandbox_root.rglob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if keyword.lower() in text.lower():
                matches.append(str(md_file.relative_to(sandbox_root)))
        if not matches:
            return f"No notes found containing '{keyword}'."
        return f"Found {len(matches)} note(s) containing '{keyword}': " + ", ".join(matches)

    async def read_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"Note not found: '{args['path']}'")
        return path.read_text(encoding="utf-8")

    async def create_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root, args["path"])
        if path.exists():
            raise ToolExecutionError(f"Note already exists: '{args['path']}' (use update_note instead)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.get("content", ""), encoding="utf-8")
        return f"Created note '{args['path']}'."

    async def update_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"Note not found: '{args['path']}' (use create_note instead)")
        mode = args.get("mode", "append")
        content = args["content"]
        if mode == "append":
            with path.open("a", encoding="utf-8") as f:
                f.write(content)
        elif mode == "overwrite":
            path.write_text(content, encoding="utf-8")
        else:
            raise ToolExecutionError(f"Unknown mode '{mode}' (expected 'append' or 'overwrite')")
        return f"Updated note '{args['path']}' (mode={mode})."

    registry.register(
        ToolSpec(
            name="search_notes",
            description=(
                "Search all Markdown notes in the sandbox for a keyword "
                "(case-insensitive substring match). Returns matching note paths."
            ),
            input_schema={
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "Keyword to search for"}},
                "required": ["keyword"],
            },
        ),
        search_notes,
    )
    registry.register(
        ToolSpec(
            name="read_note",
            description="Read the full content of a Markdown note by its path relative to the notes sandbox.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Note path, e.g. 'ideas/todo.md'"}},
                "required": ["path"],
            },
        ),
        read_note,
    )
    registry.register(
        ToolSpec(
            name="create_note",
            description="Create a new Markdown note at the given path with the given content. Fails if the note already exists.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path, e.g. 'ideas/todo.md'"},
                    "content": {"type": "string", "description": "Initial Markdown content"},
                },
                "required": ["path"],
            },
        ),
        create_note,
    )
    registry.register(
        ToolSpec(
            name="update_note",
            description="Modify an existing Markdown note: append to it, or overwrite it entirely.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path, e.g. 'ideas/todo.md'"},
                    "content": {"type": "string", "description": "Content to append, or the new full content"},
                    "mode": {
                        "type": "string",
                        "enum": ["append", "overwrite"],
                        "description": "Defaults to 'append' if omitted",
                    },
                },
                "required": ["path", "content"],
            },
        ),
        update_note,
    )
