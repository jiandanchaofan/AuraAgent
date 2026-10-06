"""Local Markdown note management tools: search_notes, read_note,
create_note, update_note, save_quick_note. All filesystem access is
confined to the notes sandbox root via tools/sandbox_path.resolve_within_sandbox().
"""
from __future__ import annotations

from typing import Any, Callable

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.notes.thino_format import insert_thino_line, parse_captured_at
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox
from tools.text_patch import apply_str_replace
from tools.workspace_root import SwappableWorkspaceRoot


def register_notes_tools(registry: ToolRegistry, sandbox_root: SwappableWorkspaceRoot) -> None:
    """Registers the four note tools against `sandbox_root.current`, read
    fresh on every call (never cached in a closure) so /notes set
    (cli/commands.py) can repoint the whole notes sandbox at runtime — e.g.
    at a real Obsidian vault — same indirection tools/files/file_tool.py
    already uses for workspace_root."""

    async def search_notes(args: dict[str, Any]) -> str:
        keyword = args["keyword"]
        root = sandbox_root.current
        matches: list[str] = []
        for md_file in sorted(root.rglob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if keyword.lower() in text.lower():
                matches.append(str(md_file.relative_to(root)))
        if not matches:
            return f"No notes found containing '{keyword}'."
        return f"Found {len(matches)} note(s) containing '{keyword}': " + ", ".join(matches)

    async def read_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root.current, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"Note not found: '{args['path']}'")
        return path.read_text(encoding="utf-8")

    async def create_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root.current, args["path"])
        if path.exists():
            raise ToolExecutionError(f"Note already exists: '{args['path']}' (use update_note instead)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.get("content", ""), encoding="utf-8")
        return f"Created note '{args['path']}'."

    async def update_note(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(sandbox_root.current, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"Note not found: '{args['path']}' (use create_note instead)")
        mode = args.get("mode", "append")
        if mode in ("append", "overwrite"):
            content = args.get("content")
            if content is None:
                raise ToolExecutionError(f"mode='{mode}' requires content.")
            if mode == "append":
                with path.open("a", encoding="utf-8") as f:
                    f.write(content)
            else:
                path.write_text(content, encoding="utf-8")
        elif mode == "str_replace":
            old_str, new_str = args.get("old_str"), args.get("new_str")
            if old_str is None or new_str is None:
                raise ToolExecutionError("mode='str_replace' requires both old_str and new_str.")
            current = path.read_text(encoding="utf-8")
            path.write_text(apply_str_replace(current, old_str, new_str), encoding="utf-8")
        else:
            raise ToolExecutionError(f"Unknown mode '{mode}' (expected 'append', 'overwrite', or 'str_replace')")
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
            description=(
                "Modify an existing Markdown note: append to it, overwrite it entirely, or replace one exact, "
                "unique piece of text in place (mode='str_replace', old_str/new_str) -- prefer str_replace for "
                "incremental edits to a large note (e.g. a Wiki entry) instead of overwriting the whole file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path, e.g. 'ideas/todo.md'"},
                    "content": {
                        "type": "string",
                        "description": "Content to append or the new full content -- required for mode='append'/'overwrite', unused for 'str_replace'",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["append", "overwrite", "str_replace"],
                        "description": "Defaults to 'append' if omitted",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "mode='str_replace' only: the exact text to replace -- must match exactly once in the note",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "mode='str_replace' only: the replacement text",
                    },
                },
                "required": ["path"],
            },
        ),
        update_note,
    )


def register_quick_note_tool(
    registry: ToolRegistry,
    sandbox_root: SwappableWorkspaceRoot,
    get_quick_notes_subdir: Callable[[], str],
) -> None:
    """Registers save_quick_note — a dedicated LLM-facing tool for "jot
    this down" requests typed directly in chat (N14). Before this
    existed, a request like "保存quick-notes: ..." had no matching tool,
    so the model improvised with create_note and produced an arbitrarily
    named new file instead of a Thino-style entry in today's daily note
    -- the exact same real bug this tool closes. Shares its formatting
    logic (tools/notes/thino_format.py) with gui/server.py's
    quick_note_sync WebSocket handler (a phone's offline sync), so a note
    saved from chat and one synced from a phone land in the identical
    place and format. `get_quick_notes_subdir` is a zero-arg callable
    (not a captured string) so /notes quickdir's updates are picked up
    immediately -- same deferred-read pattern gui/server.py's lifespan
    already uses for register_project_chat_search_tool's active-session
    lookup, needed here because this is registered before CLIContext
    exists (see core/bootstrap.py)."""

    async def save_quick_note(args: dict[str, Any]) -> str:
        text = args["text"]
        when = parse_captured_at(None)  # always "now" -- a live chat message has no other timestamp
        daily_note_path = resolve_within_sandbox(
            sandbox_root.current, f"{get_quick_notes_subdir()}/{when.strftime('%Y-%m-%d')} 日记.md"
        )
        insert_thino_line(daily_note_path, f"- {when.strftime('%H:%M:%S')} {text}")
        return f"Saved quick note to '{daily_note_path.relative_to(sandbox_root.current)}'."

    registry.register(
        ToolSpec(
            name="save_quick_note",
            description=(
                "Save a short, fragmented thought/note the user just typed directly into today's daily note, "
                "as one Thino-style bullet under the \"Today's Thino\" heading -- NOT a new standalone file. "
                "Use this whenever the user asks to jot something down / save a quick note, instead of create_note."
            ),
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The note text, verbatim (including any inline #tag)"}},
                "required": ["text"],
            },
        ),
        save_quick_note,
    )
