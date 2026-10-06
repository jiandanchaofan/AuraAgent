"""Small helpers shared by every module in tools/documents/ (PDF/Word/
PPTX/Excel) -- a file-size guard applied before attempting to parse
anything (avoids the memory/time blowup of trying to open something huge),
and an output-text truncation helper (avoids dumping an unbounded amount
of extracted text into the model's context). Not a general-purpose
utility module -- deliberately scoped to just what these four sibling
tool modules need, mirroring how tools/sandbox_path.py stays a single
focused function rather than growing into a grab-bag.
"""
from __future__ import annotations

from pathlib import Path

from core.exceptions import ToolExecutionError


def check_readable_size(path: Path, max_bytes: int) -> None:
    size = path.stat().st_size
    if size > max_bytes:
        raise ToolExecutionError(
            f"'{path.name}' is {size:,} bytes, over the {max_bytes:,}-byte limit for reading a document."
        )


def truncate_with_note(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... truncated, {len(text):,} characters total ...]"
