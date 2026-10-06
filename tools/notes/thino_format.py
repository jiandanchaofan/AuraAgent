"""Shared Thino-style quick-note formatting (N14) -- the exact convention
Obsidian's Thino plugin uses inside each day's own daily note file:
`## Today's Thino` heading, one `- HH:MM:SS <text>` bullet per entry.

Two callers share this: `tools/notes/notes_tool.py`'s `save_quick_note`
tool (a human typing "save a quick note" directly in chat) and
`gui/server.py`'s `quick_note_sync` WebSocket handler (a phone syncing an
offline-captured batch) -- both must land notes in the identical place
and format, so the logic lives here once, not duplicated.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

#: The exact heading Obsidian's Thino plugin writes quick-capture entries
#: under, inside each day's own daily note file -- matched verbatim so
#: entries from either caller land in the SAME section a human using
#: Thino directly would see, not a separate AuraAgent-only area.
THINO_HEADING = "## Today's Thino"


def parse_captured_at(captured_at: str | None) -> datetime:
    """Best-effort parse of an ISO timestamp, converted to this machine's
    local time (Obsidian daily notes are implicitly in local time, same
    as a human typing Thino directly) -- falls back to "now" for a
    missing or unparseable value, rather than dropping an otherwise-valid
    note over a timestamp problem."""
    if captured_at:
        try:
            parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.astimezone() if parsed.tzinfo is not None else parsed
    return datetime.now()


def insert_thino_line(path: Path, line: str) -> None:
    """Inserts one new Thino bullet into `path`'s `THINO_HEADING` section
    -- right after whatever the LAST existing bullet in that section is
    (or right after the heading itself if the section is still empty),
    never blindly at the end of the whole file (the daily note may well
    have other content below the Thino section). Creates the file (just
    the heading + this one line) if it doesn't exist yet, or appends the
    heading at the end of the existing file if present but missing it --
    deliberately does NOT try to replicate an Obsidian daily-note
    template, to avoid fighting whatever template the user's own vault
    already applies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(f"{THINO_HEADING}\n\n{line}\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    heading_idx = next((i for i, text_line in enumerate(lines) if text_line.strip() == THINO_HEADING), None)
    if heading_idx is None:
        new_text = "\n".join(lines).rstrip("\n")
        if new_text:
            new_text += "\n\n"
        new_text += f"{THINO_HEADING}\n\n{line}\n"
        path.write_text(new_text, encoding="utf-8")
        return

    insert_at = heading_idx + 1
    for i in range(heading_idx + 1, len(lines)):
        if lines[i].startswith("#"):
            break
        if lines[i].strip().startswith("- "):
            insert_at = i + 1
    lines.insert(insert_at, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
