"""Tests for tools/sandbox_path.py's resolve_within_sandbox() — the
generic path-traversal/absolute-path-bypass guard shared by every
filesystem-touching tool package (tools/notes/, tools/files/). Moved out
of test_notes_tool.py when the function itself moved out of
tools/notes/path_guard.py (it was never notes-specific)."""
from __future__ import annotations

import pytest

from core.exceptions import SandboxPathError
from tools.sandbox_path import resolve_within_sandbox


def test_blocks_traversal(tmp_path):
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    with pytest.raises(SandboxPathError):
        resolve_within_sandbox(sandbox, "../../etc/passwd")


def test_blocks_absolute_paths(tmp_path):
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    with pytest.raises(SandboxPathError):
        resolve_within_sandbox(sandbox, "/etc/passwd")


def test_blocks_windows_absolute_paths(tmp_path):
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    with pytest.raises(SandboxPathError):
        resolve_within_sandbox(sandbox, "C:\\Windows\\System32\\config")


def test_allows_nested_relative_path(tmp_path):
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    resolved = resolve_within_sandbox(sandbox, "ideas/todo.md")
    assert resolved == (sandbox / "ideas" / "todo.md").resolve()
