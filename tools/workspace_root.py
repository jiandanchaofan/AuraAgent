"""SwappableWorkspaceRoot — mirrors providers/swappable_provider.py's
exact indirection pattern, applied to the workspace sandbox root instead
of the LLM provider.

Every tool that reads/writes the workspace sandbox (tools/files/file_tool.py,
tools/system/screenshot_tool.py, tools/web/fetch_url_tool.py's
download_file, tools/documents/*, skills/skill_loader.py's
AURA_WORKSPACE_ROOT env var for skill subprocesses) holds a reference to the SAME
SwappableWorkspaceRoot instance instead of a bare Path captured once at
registration time. That single level of indirection is what lets the
/workspace set CLI command (cli/commands.py) repoint every one of them at
once, without reaching into each individually — each consumer must read
`.current` fresh at call time (inside its handler function body), never
cache it in a variable captured by the closure at registration time.
"""
from __future__ import annotations

from pathlib import Path


class SwappableWorkspaceRoot:
    def __init__(self, initial: Path) -> None:
        self.current = initial.resolve()
        self.current.mkdir(parents=True, exist_ok=True)

    def set_current(self, new_root: Path) -> None:
        new_root = new_root.resolve()
        new_root.mkdir(parents=True, exist_ok=True)
        self.current = new_root
