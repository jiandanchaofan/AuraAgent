"""Sandbox path resolution shared by every filesystem-touching tool.

Originally lived at tools/notes/path_guard.py — relocated here (a level
up, alongside tools/registry.py and tools/base.py) once a second tool
package (tools/files/) needed the exact same function against a different
sandbox_root. The function itself was already generic (it has never
known anything about notes specifically); only its address changed.

Every filesystem tool must resolve user/LLM-supplied relative paths
through resolve_within_sandbox() before touching disk. This is the single
choke point that blocks both path traversal (`../../etc/passwd`) and
absolute-path bypass (`/etc/passwd`, `C:\\...`), regardless of what a tool
call argument contains or which sandbox_root a particular tool package
was configured with.
"""
from __future__ import annotations

from pathlib import Path

from core.exceptions import SandboxPathError


def resolve_within_sandbox(sandbox_root: Path, relative_path: str) -> Path:
    sandbox_root = sandbox_root.resolve()

    # Reject absolute paths up front: Path(root) / "/etc/passwd" would
    # silently discard `root` entirely (pathlib join semantics), so this
    # check must happen before the join, not after.
    if Path(relative_path).is_absolute():
        raise SandboxPathError(f"Absolute paths are not allowed: '{relative_path}'")

    candidate = (sandbox_root / relative_path).resolve()
    if not candidate.is_relative_to(sandbox_root):
        raise SandboxPathError(
            f"Path '{relative_path}' resolves outside the sandbox root ({sandbox_root})"
        )
    return candidate
