"""Tests for tools/files/file_tool.py — the general file-management tools,
scoped to a configurable workspace sandbox. Path-traversal protection
itself is tested generically in tests/test_sandbox_path.py; here we only
confirm each tool actually routes through resolve_within_sandbox().
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.files.file_tool import register_file_tools
from tools.registry import ToolRegistry


def _registry(tmp_path, decision: bool = True):
    workspace = tmp_path / "workspace"
    registry = ToolRegistry()
    confirmation = FakeConfirmationChannel(decision=decision)
    register_file_tools(registry, workspace, confirmation)
    return registry, workspace, confirmation


# --- list_directory / get_file_info / search_files (read-only) --------------


@pytest.mark.asyncio
async def test_list_directory_shows_files_and_dirs(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "hi"})
    (workspace / "subdir").mkdir()

    result = await registry.dispatch("list_directory", {})

    assert "[file] a.txt" in result
    assert "[dir] subdir" in result


@pytest.mark.asyncio
async def test_list_directory_empty_reports_clearly(tmp_path):
    registry, workspace, _ = _registry(tmp_path)

    result = await registry.dispatch("list_directory", {})

    assert result == "(empty directory)"


@pytest.mark.asyncio
async def test_list_directory_rejects_non_directory(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "hi"})

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("list_directory", {"path": "a.txt"})


@pytest.mark.asyncio
async def test_list_directory_blocks_path_traversal(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("list_directory", {"path": "../../etc"})


@pytest.mark.asyncio
async def test_get_file_info_reports_size_and_kind(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "hello"})

    result = await registry.dispatch("get_file_info", {"path": "a.txt"})

    assert "file" in result
    assert "5 bytes" in result


@pytest.mark.asyncio
async def test_get_file_info_missing_path_raises(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("get_file_info", {"path": "nope.txt"})


@pytest.mark.asyncio
async def test_search_files_matches_by_name(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "report_draft.txt", "content": "x"})
    await registry.dispatch("write_file", {"path": "notes.txt", "content": "x"})

    result = await registry.dispatch("search_files", {"keyword": "report"})

    assert "report_draft.txt" in result
    assert "notes.txt" not in result


@pytest.mark.asyncio
async def test_search_files_no_match_is_friendly(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    result = await registry.dispatch("search_files", {"keyword": "nonexistent"})
    assert "No files found" in result


# --- read_file / write_file --------------------------------------------------


@pytest.mark.asyncio
async def test_write_then_read_round_trip(tmp_path):
    registry, workspace, _ = _registry(tmp_path)

    await registry.dispatch("write_file", {"path": "a.txt", "content": "hello world"})
    result = await registry.dispatch("read_file", {"path": "a.txt"})

    assert result == "hello world"


@pytest.mark.asyncio
async def test_write_file_create_only_fails_if_exists(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "1"})

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("write_file", {"path": "a.txt", "content": "2"})


@pytest.mark.asyncio
async def test_write_file_overwrite_replaces_content_without_confirmation(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=False)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "1"})

    await registry.dispatch("write_file", {"path": "a.txt", "content": "2", "mode": "overwrite"})

    result = await registry.dispatch("read_file", {"path": "a.txt"})
    assert result == "2"
    assert confirmation.requests == []  # overwrite never asks, mirrors update_note


@pytest.mark.asyncio
async def test_write_file_creates_parent_directories(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    await registry.dispatch("write_file", {"path": "a/b/c.txt", "content": "x"})
    assert (workspace / "a" / "b" / "c.txt").is_file()


@pytest.mark.asyncio
async def test_read_file_missing_raises(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("read_file", {"path": "missing.txt"})


@pytest.mark.asyncio
async def test_read_file_rejects_known_binary_extension(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    (workspace / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("read_file", {"path": "image.png"})


@pytest.mark.asyncio
async def test_read_file_blocks_path_traversal(tmp_path):
    registry, workspace, _ = _registry(tmp_path)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("read_file", {"path": "../../etc/passwd"})


# --- delete_file (always confirmed) ------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_approved_removes_it(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=True)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "x"})

    result = await registry.dispatch("delete_file", {"path": "a.txt"})

    assert "Deleted" in result
    assert not (workspace / "a.txt").exists()
    assert len(confirmation.requests) == 1
    assert confirmation.requests[0].risk_level == "destructive"


@pytest.mark.asyncio
async def test_delete_file_declined_keeps_it(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=False)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "x"})

    result = await registry.dispatch("delete_file", {"path": "a.txt"})

    assert "declined" in result.lower()
    assert (workspace / "a.txt").is_file()


@pytest.mark.asyncio
async def test_delete_file_missing_raises_before_asking(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=True)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("delete_file", {"path": "missing.txt"})
    assert confirmation.requests == []


# --- move_file / copy_file (confirmed only on overwrite) --------------------


@pytest.mark.asyncio
async def test_move_file_to_new_location_no_confirmation_needed(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=False)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "x"})

    result = await registry.dispatch("move_file", {"source": "a.txt", "destination": "b.txt"})

    assert "Moved" in result
    assert not (workspace / "a.txt").exists()
    assert (workspace / "b.txt").is_file()
    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_move_file_onto_existing_destination_requires_confirmation(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=True)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "new"})
    await registry.dispatch("write_file", {"path": "b.txt", "content": "old"})

    result = await registry.dispatch("move_file", {"source": "a.txt", "destination": "b.txt"})

    assert "Moved" in result
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "new"
    assert len(confirmation.requests) == 1


@pytest.mark.asyncio
async def test_move_file_onto_existing_destination_declined_keeps_both(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=False)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "new"})
    await registry.dispatch("write_file", {"path": "b.txt", "content": "old"})

    result = await registry.dispatch("move_file", {"source": "a.txt", "destination": "b.txt"})

    assert "declined" in result.lower()
    assert (workspace / "a.txt").is_file()
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_move_file_missing_source_raises(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=True)
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("move_file", {"source": "missing.txt", "destination": "b.txt"})


@pytest.mark.asyncio
async def test_copy_file_to_new_location_no_confirmation_and_source_remains(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=False)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "x"})

    result = await registry.dispatch("copy_file", {"source": "a.txt", "destination": "b.txt"})

    assert "Copied" in result
    assert (workspace / "a.txt").is_file()
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "x"
    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_copy_file_onto_existing_destination_requires_confirmation(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path, decision=True)
    await registry.dispatch("write_file", {"path": "a.txt", "content": "new"})
    await registry.dispatch("write_file", {"path": "b.txt", "content": "old"})

    await registry.dispatch("copy_file", {"source": "a.txt", "destination": "b.txt"})

    assert (workspace / "b.txt").read_text(encoding="utf-8") == "new"
    assert len(confirmation.requests) == 1
