"""read_docx/create_docx/edit_docx -- Word document read/create/edit via
python-docx. Supersedes the earlier skills_store/docx_text_extract Skill
(a hand-rolled ZIP+XML parser that only read paragraph text and, being a
Skill subprocess, had to work around its own cwd not being the workspace
-- see that Skill's own post-mortem). As a native tool this reuses
resolve_within_sandbox()/workspace_root directly, the same way every
other tools/files/-style tool does, so that whole class of bug does not
exist here to begin with.

edit_docx is deliberately a small, well-defined operation set (append
paragraphs, whole-paragraph find/replace) rather than a general editor --
see its own docstring below for exactly what it can and cannot preserve.
"""
from __future__ import annotations

from typing import Any

import docx

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.documents._common import check_readable_size, truncate_with_note
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox
from tools.workspace_root import SwappableWorkspaceRoot


def _read_docx_text(path) -> str:
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text]

    if document.tables:
        parts.append("--- Tables ---")
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))

    return "\n".join(parts)


def register_docx_tools(
    registry: ToolRegistry,
    workspace_root: SwappableWorkspaceRoot,
    max_read_bytes: int,
    max_extract_chars: int,
) -> None:
    async def read_docx(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root.current, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"File not found: '{args['path']}'")
        check_readable_size(path, max_read_bytes)
        try:
            text = _read_docx_text(path)
        except Exception as exc:  # noqa: BLE001 - python-docx raises several different exception types for malformed input
            raise ToolExecutionError(f"Could not open '{args['path']}' as a .docx: {exc}") from exc
        return truncate_with_note(text, max_extract_chars)

    registry.register(
        ToolSpec(
            name="read_docx",
            description=(
                "Read the text content of a .docx (Word) file in the workspace -- paragraphs, plus any "
                "tables (rendered as rows of cell text joined with ' | ')."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to the workspace."}},
                "required": ["path"],
            },
        ),
        read_docx,
    )

    async def create_docx(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root.current, args["path"])
        mode = args.get("mode", "create_only")
        if mode not in ("create_only", "overwrite"):
            raise ToolExecutionError(f"Unknown mode '{mode}' (expected 'create_only' or 'overwrite').")
        if mode == "create_only" and path.exists():
            raise ToolExecutionError(f"'{args['path']}' already exists (use mode='overwrite' to replace it).")

        document = docx.Document()
        if args.get("title"):
            document.add_heading(args["title"], level=1)
        for paragraph in args.get("paragraphs", []):
            document.add_paragraph(paragraph)

        path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(path))
        return f"Created '{args['path']}' with {len(args.get('paragraphs', []))} paragraph(s)."

    registry.register(
        ToolSpec(
            name="create_docx",
            description=(
                "Create a new .docx (Word) file in the workspace from a title and a list of body "
                "paragraphs. mode='create_only' (default) fails if the file already exists; "
                "mode='overwrite' replaces it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace."},
                    "title": {"type": "string", "description": "Optional document title, rendered as a Heading 1."},
                    "paragraphs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Body paragraphs, in order.",
                    },
                    "mode": {"type": "string", "enum": ["create_only", "overwrite"], "description": "Defaults to 'create_only'."},
                },
                "required": ["path"],
            },
        ),
        create_docx,
    )

    async def edit_docx(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root.current, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"File not found: '{args['path']}'")

        document = docx.Document(str(path))
        changed = False

        for paragraph_text in args.get("append_paragraphs") or []:
            document.add_paragraph(paragraph_text)
            changed = True

        find = args.get("find")
        replaced_count = 0
        if find:
            replace = args.get("replace", "")
            for paragraph in document.paragraphs:
                if find not in paragraph.text:
                    continue
                new_text = paragraph.text.replace(find, replace)
                # Whole-paragraph replace, not a surgical run-level edit:
                # a paragraph's text can be split across multiple <w:r>
                # runs (Word itself does this, e.g. after spell-check), so
                # a find/replace that tried to edit runs in place could
                # easily corrupt or miss a match spanning a run boundary.
                # Collapsing to one run is reliable but DOES lose any
                # per-run formatting mixed within that paragraph (e.g.
                # only part of it bold) -- see this tool's own description.
                for run in paragraph.runs:
                    run.text = ""
                if paragraph.runs:
                    paragraph.runs[0].text = new_text
                else:
                    paragraph.add_run(new_text)
                replaced_count += 1
                changed = True

        if not changed:
            return "No changes made (nothing to append, and no paragraph matched `find`)."

        document.save(str(path))
        appended = len(args.get("append_paragraphs") or [])
        return f"Updated '{args['path']}': appended {appended} paragraph(s), replaced text in {replaced_count} paragraph(s)."

    registry.register(
        ToolSpec(
            name="edit_docx",
            description=(
                "Make a small, well-defined edit to an EXISTING .docx file: append new paragraphs to the "
                "end, and/or replace a piece of text everywhere it appears (whole paragraphs only). This "
                "is NOT a general-purpose editor -- a paragraph whose text is replaced loses any mixed "
                "per-run formatting within it (e.g. only part of the paragraph being bold), collapsing to "
                "a single run. For precise, formatting-preserving edits, this tool cannot help."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace."},
                    "append_paragraphs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New paragraphs to add at the end of the document.",
                    },
                    "find": {"type": "string", "description": "Text to search for, within each paragraph."},
                    "replace": {"type": "string", "description": "Replacement text (used with `find`)."},
                },
                "required": ["path"],
            },
        ),
        edit_docx,
    )
