"""Tests for the Markdown note tools. See tests/test_sandbox_path.py for
the generic path-traversal/absolute-path-bypass protection tests
(resolve_within_sandbox() itself is not notes-specific)."""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.notes.notes_tool import register_notes_tools, register_quick_note_tool
from tools.registry import ToolRegistry
from tools.workspace_root import SwappableWorkspaceRoot


@pytest.fixture
def registry_and_sandbox(tmp_path):
    sandbox = SwappableWorkspaceRoot(tmp_path / "notes")
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


# --- update_note mode="str_replace" (N14 -- Wiki Curator incremental edits) --


@pytest.mark.asyncio
async def test_update_note_str_replace_replaces_unique_match(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "wiki/concept.md", "content": "# Concept\nStatus: draft\n"})

    await registry.dispatch(
        "update_note",
        {"path": "wiki/concept.md", "mode": "str_replace", "old_str": "Status: draft", "new_str": "Status: final"},
    )

    result = await registry.dispatch("read_note", {"path": "wiki/concept.md"})
    assert "Status: final" in result
    assert "Status: draft" not in result


@pytest.mark.asyncio
async def test_update_note_str_replace_zero_matches_raises(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "a.md", "content": "hello"})

    with pytest.raises(ToolExecutionError, match="not found"):
        await registry.dispatch("update_note", {"path": "a.md", "mode": "str_replace", "old_str": "nope", "new_str": "x"})


@pytest.mark.asyncio
async def test_update_note_str_replace_multiple_matches_raises(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "a.md", "content": "dup\ndup"})

    with pytest.raises(ToolExecutionError, match="matches 2 times"):
        await registry.dispatch("update_note", {"path": "a.md", "mode": "str_replace", "old_str": "dup", "new_str": "x"})


@pytest.mark.asyncio
async def test_update_note_str_replace_requires_both_old_and_new(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "a.md", "content": "hello"})

    with pytest.raises(ToolExecutionError, match="requires both"):
        await registry.dispatch("update_note", {"path": "a.md", "mode": "str_replace", "old_str": "hello"})


@pytest.mark.asyncio
async def test_update_note_append_still_requires_content(registry_and_sandbox):
    registry, _sandbox = registry_and_sandbox
    await registry.dispatch("create_note", {"path": "a.md", "content": "hello"})

    with pytest.raises(ToolExecutionError, match="requires content"):
        await registry.dispatch("update_note", {"path": "a.md", "mode": "append"})


# --- save_quick_note (N14 -- a human typing "save a quick note" in chat) ---


@pytest.mark.asyncio
async def test_save_quick_note_writes_a_thino_bullet_into_todays_daily_note(tmp_path):
    from datetime import datetime

    sandbox = SwappableWorkspaceRoot(tmp_path / "notes")
    registry = ToolRegistry()
    register_quick_note_tool(registry, sandbox, lambda: "Daily Notes")

    result = await registry.dispatch("save_quick_note", {"text": "这两天开发AuraAgent，有非常多的收获"})

    today = datetime.now().strftime("%Y-%m-%d")
    daily_note = sandbox.current / "Daily Notes" / f"{today} 日记.md"
    assert daily_note.is_file()
    content = daily_note.read_text(encoding="utf-8")
    assert "## Today's Thino" in content
    assert "这两天开发AuraAgent，有非常多的收获" in content
    assert str(daily_note.relative_to(sandbox.current)) in result


@pytest.mark.asyncio
async def test_save_quick_note_follows_the_live_subdir_getter(tmp_path):
    """get_quick_notes_subdir is read fresh on every call, not captured
    once at registration -- /notes quickdir's updates must take effect
    immediately, same as every other Swappable-style indirection in this
    project."""
    sandbox = SwappableWorkspaceRoot(tmp_path / "notes")
    registry = ToolRegistry()
    current_subdir = "Daily Notes"
    register_quick_note_tool(registry, sandbox, lambda: current_subdir)

    current_subdir = "Journal"
    await registry.dispatch("save_quick_note", {"text": "after switching subdir"})

    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    assert (sandbox.current / "Journal" / f"{today} 日记.md").is_file()
    assert not (sandbox.current / "Daily Notes" / f"{today} 日记.md").exists()
