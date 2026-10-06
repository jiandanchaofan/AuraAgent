"""apply_str_replace — the shared matching logic behind `mode="str_replace"`
on tools/notes/notes_tool.py::update_note and tools/files/file_tool.py::
write_file (N14, the "Wiki Curator" use case: incrementally patching a
large Markdown file instead of reading it whole and writing the whole
thing back). One function, not duplicated in each tool module, so both
stay behaviorally identical.

`old_str` must match EXACTLY ONCE in `text` -- the same convention
Anthropic's own file-editing tools use (and for the same reason): zero
matches usually means stale/misremembered context (the file changed since
it was last read), and more than one match means the edit is ambiguous
about which occurrence was intended. Both are reported as a clear
ToolExecutionError rather than silently guessing (e.g. "replace the
first one"), which would risk corrupting Wiki content the Wiki Curator
role is specifically meant to maintain carefully.
"""
from __future__ import annotations

from core.exceptions import ToolExecutionError


def apply_str_replace(text: str, old_str: str, new_str: str) -> str:
    count = text.count(old_str)
    if count == 0:
        raise ToolExecutionError(
            "old_str was not found in the file -- it may have changed since you last read it. "
            "Re-read the file and try again with the exact current text."
        )
    if count > 1:
        raise ToolExecutionError(
            f"old_str matches {count} times -- it must match exactly once. "
            "Include more surrounding context to make it unique."
        )
    return text.replace(old_str, new_str, 1)
