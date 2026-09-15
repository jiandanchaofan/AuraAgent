"""Tests for the Markdown note tools, including sandbox path-traversal
and absolute-path-bypass protection.
"""
from __future__ import annotations

import pytest

from core.exceptions import SandboxPathError, ToolExecutionError
from tools.notes.notes_tool import register_notes_tools
from tools.notes.path_guard import resolve_within_sandbox
from tools.registry import ToolRegistry


@pytest.fixture
def registry_and_sandbox(tmp_path):
    sandbox = tmp_path / "notes"
    registry = ToolRegistry()
    register_notes_tools(registry, sandbox)
    return registry, sandbox


@pytest.mark.asyncio
async def test_create_read_update_search_round_trip(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox

    create_result = await registry.dispatch("create_note", {"path": "todo.md", "content": "# Todo\n- buy milk\n"})
    assert "Created" in create_result

    read_result = await registry.dispatch("read_note", {"path": "todo.md"})
    assert "buy milk" in read_result

    await registry.dispatch("update_note", {"path": "todo.md", "content": "- walk dog\n", "mode": "append"})
    read_after_update = await registry.dispatch("read_note", {"path": "todo.md"})
    assert "walk dog" in read_after_update and "buy milk" in read_after_update

    search_result = await registry.dispatch("search_notes", {"keyword": "walk dog"})
    assert "todo.md" in search_result


@pytest.mark.asyncio
async def test_create_note_fails_if_exists(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "a.md", "content": "x"})
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("create_note", {"path": "a.md", "content": "y"})


@pytest.mark.asyncio
async def test_read_note_missing_raises(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("read_note", {"path": "missing.md"})


@pytest.mark.asyncio
async def test_read_note_blocks_path_traversal(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("read_note", {"path": "../../etc/passwd"})


def test_path_guard_blocks_traversal(tmp_path):
    sandbox = tmp_path / "notes"
    sandbox.mkdir()
    with pytest.raises(SandboxPathError):
        resolve_within_sandbox(sandbox, "../../etc/passwd")


def test_path_guard_blocks_absolute_paths(tmp_path):
    sandbox = tmp_path / "notes"
    sandbox.mkdir()
    with pytest.raises(SandboxPathError):
        resolve_within_sandbox(sandbox, "/etc/passwd")


def test_path_guard_allows_nested_relative_path(tmp_path):
    sandbox = tmp_path / "notes"
    sandbox.mkdir()
    resolved = resolve_within_sandbox(sandbox, "ideas/todo.md")
    assert resolved == (sandbox / "ideas" / "todo.md").resolve()
