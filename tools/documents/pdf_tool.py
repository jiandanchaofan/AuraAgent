"""read_pdf -- read-only PDF text extraction via pypdf (pure Python, no
external binary dependency). PDF is the one format in tools/documents/
with no create/edit counterpart: generating/editing PDF layout properly
is a much bigger undertaking than the other three formats (which all
have a mature, bidirectional Python library) and nothing in this project
has needed it yet -- if that changes, it's a separate, later addition.

Known, permanent limitation: a scanned/image-only PDF has no embedded
text layer, so this returns little or nothing for one -- there is no OCR
step here (a real, heavier dependency this project doesn't currently
need), and the tool description says so plainly rather than silently
returning an empty string and looking like a bug.
"""
from __future__ import annotations

from typing import Any

from pypdf import PdfReader

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.documents._common import check_readable_size, truncate_with_note
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox
from tools.workspace_root import SwappableWorkspaceRoot


def _parse_page_range(spec: str, total_pages: int) -> list[int]:
    """"5" -> just page 5; "2-4" -> pages 2 through 4. Returns 0-indexed
    page numbers into pypdf's page list."""
    spec = spec.strip()
    if "-" in spec:
        start_str, _, end_str = spec.partition("-")
    else:
        start_str = end_str = spec
    try:
        start, end = int(start_str), int(end_str)
    except ValueError as exc:
        raise ToolExecutionError(f"Invalid page range '{spec}' (expected e.g. '3' or '2-4').") from exc
    if start < 1 or end < start:
        raise ToolExecutionError(f"Invalid page range '{spec}'.")
    if start > total_pages:
        raise ToolExecutionError(f"Page range '{spec}' starts beyond the document's {total_pages} page(s).")
    return list(range(start - 1, min(end, total_pages)))


def register_pdf_tools(
    registry: ToolRegistry,
    workspace_root: SwappableWorkspaceRoot,
    max_read_bytes: int,
    max_extract_chars: int,
) -> None:
    async def read_pdf(args: dict[str, Any]) -> str:
        path = resolve_within_sandbox(workspace_root.current, args["path"])
        if not path.is_file():
            raise ToolExecutionError(f"File not found: '{args['path']}'")
        check_readable_size(path, max_read_bytes)

        try:
            reader = PdfReader(str(path))
        except Exception as exc:  # noqa: BLE001 - pypdf raises several different exception types for malformed input
            raise ToolExecutionError(f"Could not open '{args['path']}' as a PDF: {exc}") from exc

        total_pages = len(reader.pages)
        page_indices = _parse_page_range(args["pages"], total_pages) if args.get("pages") else range(total_pages)

        parts = []
        for i in page_indices:
            text = reader.pages[i].extract_text() or ""
            parts.append(f"--- Page {i + 1} ---\n{text}")
        return truncate_with_note("\n\n".join(parts), max_extract_chars)

    registry.register(
        ToolSpec(
            name="read_pdf",
            description=(
                "Extract text from a .pdf file in the workspace. Only works on PDFs with an embedded text "
                "layer -- a scanned/image-only PDF has no text to extract (no OCR is performed) and will "
                "return little or nothing for those pages. Omit `pages` to read the whole document."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "PDF file path relative to the workspace."},
                    "pages": {
                        "type": "string",
                        "description": "Optional page range, e.g. '3' or '2-4'. Omit to read every page.",
                    },
                },
                "required": ["path"],
            },
        ),
        read_pdf,
    )
