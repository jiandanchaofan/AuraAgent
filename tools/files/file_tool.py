"""General local file management: list_directory, read_file, write_file,
delete_file, move_file, copy_file, get_file_info, search_files. All
filesystem access is confined to a configurable workspace sandbox root
via tools/sandbox_path.resolve_within_sandbox() — the same guard
tools/notes/ uses, pointed at settings.workspace_root instead of
settings.notes_sandbox_root. Unlike the other fixed sandbox roots,
workspace_root is meant to be pointed at a user's real working directory
via AURA_WORKSPACE_ROOT (see config/settings.py) — the boundary itself
never goes away, only its location is configurable.

Destructive/overwrite-risking operations go through ConfirmationChannel,
the same pattern tools/tasks/task_tool.py's delete_task established:
  - delete_file: ALWAYS confirmed (irreversible).
  - move_file/copy_file: confirmed ONLY when the destination already
    exists (would silently overwrite it) — moving/copying into a new
    location is not gated, matching the principle that only genuinely
    risky operations pay the confirmation cost.
  - write_file(mode="overwrite") is NOT gated, mirroring
    tools/notes/notes_tool.py's update_note(mode="overwrite"), which has
    never required confirmation either — consistency with that existing
    precedent, not a new judgment call made here.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox

# A conservative guess at "this is probably not text" — read_file refuses
# to decode these rather than dumping binary noise into the LLM's context
# window. Not exhaustive; a real binary file with an unrecognized
# extension still gets a clear error via the UnicodeDecodeError catch below.
_LIKELY_BINARY_SUFFIXES = {
    ".exe", ".dll", ".so", ".bin", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".pyc", ".pyo", ".db", ".sqlite",
}


def register_file_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    confirmation_channel: ConfirmationChannel,
) -> None:
    """Registers the eight file tools against `workspace_root`, creating it if needed."""
    workspace_root.mkdir(parents=True, exist_ok=True)

    async def list_directory(args: dict[str, Any]) -> str:
        rel = args.get("path", "")
        path = resolve_within_sandbox(workspace_root, rel)
        if not path.is_dir():
            raise ToolExecutionError(f"Not a directory: '{rel}'")
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            return "(empty directory)"
        lines = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"- [dir] {entry.name}")
            else:
                lines.append(f"- [file] {entry.name}, {entry.stat().st_size} bytes")
        return "\n".join(lines)

    async def read_file(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"File not found: '{args['path']}'")
        if path.suffix.lower() in _LIKELY_BINARY_SUFFIXES:
            raise ToolExecutionError(
                f"'{args['path']}' looks like a binary file ({path.suffix}) — read_file only supports text."
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(f"'{args['path']}' is not a UTF-8 text file: {exc}") from exc

    async def write_file(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root, args["path"])
        mode = args.get("mode", "create_only")
        if mode not in ("create_only", "overwrite"):
            raise ToolExecutionError(f"Unknown mode '{mode}' (expected 'create_only' or 'overwrite').")
        if mode == "create_only" and path.exists():
            raise ToolExecutionError(f"'{args['path']}' already exists (use mode='overwrite' to replace it).")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.get("content", ""), encoding="utf-8")
        return f"Wrote '{args['path']}' (mode={mode})."

    async def delete_file(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"File not found: '{args['path']}'")

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="delete_file",
                arguments=args,
                reason=f"Delete file '{args['path']}'? This cannot be undone.",
                risk_level="destructive",
            )
        )
        if not approved:
            return f"User declined to delete '{args['path']}'."
        path.unlink()
        return f"Deleted '{args['path']}'."

    async def move_file(args: dict[str, Any]) -> str:
        source = resolve_within_sandbox(workspace_root, args["source"])
        destination = resolve_within_sandbox(workspace_root, args["destination"])
        if not source.exists():
            raise ToolExecutionError(f"Source not found: '{args['source']}'")
        if destination.exists():
            approved = await confirmation_channel.confirm(
                ConfirmationRequest(
                    tool_name="move_file",
                    arguments=args,
                    reason=(
                        f"'{args['destination']}' already exists — moving '{args['source']}' there "
                        "will overwrite it. Proceed?"
                    ),
                    risk_level="destructive",
                )
            )
            if not approved:
                return f"User declined to overwrite '{args['destination']}'."
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return f"Moved '{args['source']}' to '{args['destination']}'."

    async def copy_file(args: dict[str, Any]) -> str:
        source = resolve_within_sandbox(workspace_root, args["source"])
        destination = resolve_within_sandbox(workspace_root, args["destination"])
        if not source.is_file():
            raise ToolExecutionError(f"Source file not found: '{args['source']}'")
        if destination.exists():
            approved = await confirmation_channel.confirm(
                ConfirmationRequest(
                    tool_name="copy_file",
                    arguments=args,
                    reason=(
                        f"'{args['destination']}' already exists — copying '{args['source']}' there "
                        "will overwrite it. Proceed?"
                    ),
                    risk_level="destructive",
                )
            )
            if not approved:
                return f"User declined to overwrite '{args['destination']}'."
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))
        return f"Copied '{args['source']}' to '{args['destination']}'."

    async def get_file_info(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root, args["path"])
        if not path.exists():
            raise ToolExecutionError(f"Path not found: '{args['path']}'")
        stat = path.stat()
        kind = "directory" if path.is_dir() else "file"
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return f"'{args['path']}': {kind}, {stat.st_size} bytes, last modified {modified}"

    async def search_files(args: dict[str, Any]) -> str:
        keyword = args["keyword"].lower()
        rel = args.get("path", "")
        start = resolve_within_sandbox(workspace_root, rel)
        if not start.is_dir():
            raise ToolExecutionError(f"Not a directory: '{rel}'")
        matches = [
            str(p.relative_to(workspace_root))
            for p in sorted(start.rglob("*"))
            if p.is_file() and keyword in p.name.lower()
        ]
        if not matches:
            return f"No files found matching '{args['keyword']}'."
        return f"Found {len(matches)} file(s): " + ", ".join(matches)

    registry.register(
        ToolSpec(
            name="list_directory",
            description="List files and subdirectories at a path within the workspace (defaults to the workspace root).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path, e.g. 'projects/foo'. Omit for the workspace root."}},
                "required": [],
            },
        ),
        list_directory,
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read the full text content of a file in the workspace. Only text files are supported.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to the workspace."}},
                "required": ["path"],
            },
        ),
        read_file,
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Create or overwrite a text file in the workspace. mode='create_only' (default) fails if it already exists; mode='overwrite' replaces it.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace."},
                    "content": {"type": "string", "description": "Text content to write."},
                    "mode": {"type": "string", "enum": ["create_only", "overwrite"], "description": "Defaults to 'create_only'."},
                },
                "required": ["path"],
            },
        ),
        write_file,
    )
    registry.register(
        ToolSpec(
            name="delete_file",
            description="Delete a file in the workspace. Always asks the user to confirm before deleting.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to the workspace."}},
                "required": ["path"],
            },
        ),
        delete_file,
    )
    registry.register(
        ToolSpec(
            name="move_file",
            description="Move or rename a file within the workspace. Asks for confirmation only if the destination already exists (would overwrite it).",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing file path relative to the workspace."},
                    "destination": {"type": "string", "description": "New file path relative to the workspace."},
                },
                "required": ["source", "destination"],
            },
        ),
        move_file,
    )
    registry.register(
        ToolSpec(
            name="copy_file",
            description="Copy a file within the workspace. Asks for confirmation only if the destination already exists (would overwrite it).",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing file path relative to the workspace."},
                    "destination": {"type": "string", "description": "New file path relative to the workspace."},
                },
                "required": ["source", "destination"],
            },
        ),
        copy_file,
    )
    registry.register(
        ToolSpec(
            name="get_file_info",
            description="Get metadata (size, file/directory, last-modified time) for a path in the workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the workspace."}},
                "required": ["path"],
            },
        ),
        get_file_info,
    )
    registry.register(
        ToolSpec(
            name="search_files",
            description="Recursively search for files whose NAME contains a keyword (case-insensitive), starting from a path within the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Substring to match against file names."},
                    "path": {"type": "string", "description": "Directory to start from. Omit for the workspace root."},
                },
                "required": ["keyword"],
            },
        ),
        search_files,
    )
