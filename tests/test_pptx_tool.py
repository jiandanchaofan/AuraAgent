"""Tests for tools/documents/pptx_tool.py -- read_pptx/create_pptx/
edit_pptx. Re-covers the regression points the retired skills_store/
make_pptx/ Skill used to be tested for directly (real .pptx generation,
workspace-relative saving including a not-yet-existing subdirectory,
path-traversal rejection, absolute-path rejection) plus create-then-read
round trips for the tool's own behavior.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.documents.pptx_tool import register_pptx_tools
from tools.registry import ToolRegistry
from tools.workspace_root import SwappableWorkspaceRoot


def _registry(tmp_path, max_read_bytes=20_000_000, max_extract_chars=50_000):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    register_pptx_tools(registry, SwappableWorkspaceRoot(workspace), max_read_bytes, max_extract_chars)
    return registry, workspace


@pytest.mark.asyncio
async def test_create_pptx_generates_a_real_pptx_file(tmp_path):
    registry, workspace = _registry(tmp_path)

    result = await registry.dispatch(
        "create_pptx",
        {
            "path": "demo.pptx",
            "title": "Test Deck",
            "slides": [{"title": "Slide One", "bullets": ["Point A", "Point B"]}],
        },
    )

    output_path = workspace / "demo.pptx"
    assert output_path.is_file()
    assert output_path.stat().st_size > 1000  # a real .pptx zip archive, not an empty/error stub
    assert "demo.pptx" in result


@pytest.mark.asyncio
async def test_create_pptx_saves_within_workspace_with_subdirectory(tmp_path):
    registry, workspace = _registry(tmp_path)

    result = await registry.dispatch(
        "create_pptx",
        {
            "path": "reports/deck.pptx",  # a subdirectory that does not exist yet
            "title": "Test Deck",
            "slides": [{"title": "Slide One", "bullets": ["Point A", "Point B"]}],
        },
    )

    saved_path = workspace / "reports" / "deck.pptx"
    assert saved_path.is_file()
    assert saved_path.stat().st_size > 1000
    assert "reports/deck.pptx" in result


@pytest.mark.asyncio
async def test_create_pptx_rejects_a_path_traversal_output_path(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="outside the sandbox root"):
        await registry.dispatch(
            "create_pptx", {"path": "../escaped.pptx", "title": "x", "slides": []}
        )


@pytest.mark.asyncio
async def test_create_pptx_rejects_an_absolute_output_path(tmp_path):
    registry, _ = _registry(tmp_path)
    absolute_target = tmp_path / "elsewhere.pptx"

    with pytest.raises(ToolExecutionError, match="Absolute paths are not allowed"):
        await registry.dispatch(
            "create_pptx", {"path": str(absolute_target), "title": "x", "slides": []}
        )
    assert not absolute_target.exists()


@pytest.mark.asyncio
async def test_create_then_read_round_trip(tmp_path):
    registry, _ = _registry(tmp_path)

    await registry.dispatch(
        "create_pptx",
        {
            "path": "demo.pptx",
            "title": "Deck Title",
            "slides": [{"title": "Slide One", "bullets": ["Point A", "Point B"]}],
        },
    )
    result = await registry.dispatch("read_pptx", {"path": "demo.pptx"})

    assert "Slide One" in result
    assert "- Point A" in result
    assert "- Point B" in result


@pytest.mark.asyncio
async def test_create_pptx_create_only_fails_if_exists(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_pptx", {"path": "demo.pptx", "title": "x", "slides": []})

    with pytest.raises(ToolExecutionError, match="already exists"):
        await registry.dispatch("create_pptx", {"path": "demo.pptx", "title": "y", "slides": []})


@pytest.mark.asyncio
async def test_create_pptx_overwrite_replaces(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch(
        "create_pptx", {"path": "demo.pptx", "title": "Original", "slides": [{"title": "A", "bullets": ["one"]}]}
    )

    await registry.dispatch(
        "create_pptx",
        {"path": "demo.pptx", "title": "Replaced", "slides": [{"title": "B", "bullets": ["two"]}], "mode": "overwrite"},
    )
    result = await registry.dispatch("read_pptx", {"path": "demo.pptx"})

    assert "two" in result
    assert "one" not in result


@pytest.mark.asyncio
async def test_edit_pptx_appends_slides(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch(
        "create_pptx", {"path": "demo.pptx", "title": "Deck", "slides": [{"title": "First", "bullets": ["a"]}]}
    )

    await registry.dispatch(
        "edit_pptx", {"path": "demo.pptx", "append_slides": [{"title": "Second", "bullets": ["b"]}]}
    )
    result = await registry.dispatch("read_pptx", {"path": "demo.pptx"})

    assert "First" in result
    assert "Second" in result
    assert "- b" in result


@pytest.mark.asyncio
async def test_edit_pptx_no_changes_when_append_slides_empty(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_pptx", {"path": "demo.pptx", "title": "Deck", "slides": []})

    result = await registry.dispatch("edit_pptx", {"path": "demo.pptx", "append_slides": []})

    assert "No changes made" in result


@pytest.mark.asyncio
async def test_edit_pptx_missing_file(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="File not found"):
        await registry.dispatch(
            "edit_pptx", {"path": "missing.pptx", "append_slides": [{"title": "x", "bullets": []}]}
        )


@pytest.mark.asyncio
async def test_read_pptx_rejects_oversized_file(tmp_path):
    registry, _ = _registry(tmp_path, max_read_bytes=10)
    await registry.dispatch("create_pptx", {"path": "demo.pptx", "title": "Deck", "slides": []})

    with pytest.raises(ToolExecutionError, match="over the"):
        await registry.dispatch("read_pptx", {"path": "demo.pptx"})


@pytest.mark.asyncio
async def test_read_pptx_truncates_long_output(tmp_path):
    registry, _ = _registry(tmp_path, max_extract_chars=10)
    await registry.dispatch(
        "create_pptx",
        {"path": "demo.pptx", "title": "Deck", "slides": [{"title": "A slide with a longer title than the cap", "bullets": ["bullet text here"]}]},
    )

    result = await registry.dispatch("read_pptx", {"path": "demo.pptx"})

    assert "truncated" in result
