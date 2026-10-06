"""Tests for tools/documents/xlsx_tool.py -- read_xlsx/create_xlsx/
edit_xlsx. Create-then-read round trips, no external fixture files
needed, plus the sandbox/size/mode-guard cases every tools/documents/
module shares.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.documents.xlsx_tool import register_xlsx_tools
from tools.registry import ToolRegistry
from tools.workspace_root import SwappableWorkspaceRoot


def _registry(tmp_path, max_read_bytes=20_000_000, max_extract_chars=50_000):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    register_xlsx_tools(registry, SwappableWorkspaceRoot(workspace), max_read_bytes, max_extract_chars)
    return registry, workspace


@pytest.mark.asyncio
async def test_create_then_list_sheets(tmp_path):
    registry, _ = _registry(tmp_path)

    await registry.dispatch(
        "create_xlsx",
        {
            "path": "book.xlsx",
            "sheets": [
                {"name": "Sheet1", "rows": [["Name", "Score"], ["Alice", 9]]},
                {"name": "Sheet2", "rows": [["x"]]},
            ],
        },
    )
    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx"})

    assert "Sheet1" in result
    assert "Sheet2" in result


@pytest.mark.asyncio
async def test_create_then_read_a_sheet(tmp_path):
    registry, _ = _registry(tmp_path)

    await registry.dispatch(
        "create_xlsx",
        {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["Name", "Score"], ["Alice", 9]]}]},
    )
    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "Name, Score" in result
    assert "Alice, 9" in result


@pytest.mark.asyncio
async def test_read_xlsx_unknown_sheet(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": []}]})

    with pytest.raises(ToolExecutionError, match="No sheet named"):
        await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Missing"})


@pytest.mark.asyncio
async def test_create_xlsx_saves_within_workspace_with_subdirectory(tmp_path):
    registry, workspace = _registry(tmp_path)

    result = await registry.dispatch("create_xlsx", {"path": "reports/book.xlsx", "sheets": [{"name": "S", "rows": [["a"]]}]})

    saved_path = workspace / "reports" / "book.xlsx"
    assert saved_path.is_file()
    assert "reports/book.xlsx" in result


@pytest.mark.asyncio
async def test_create_xlsx_create_only_fails_if_exists(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "S", "rows": []}]})

    with pytest.raises(ToolExecutionError, match="already exists"):
        await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "S", "rows": []}]})


@pytest.mark.asyncio
async def test_create_xlsx_overwrite_replaces(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["original"]]}]})

    await registry.dispatch(
        "create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["replaced"]]}], "mode": "overwrite"}
    )
    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "replaced" in result
    assert "original" not in result


@pytest.mark.asyncio
async def test_create_xlsx_requires_at_least_one_sheet(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="at least one sheet"):
        await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": []})


@pytest.mark.asyncio
async def test_edit_xlsx_append_rows(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["Name", "Score"]]}]})

    await registry.dispatch("edit_xlsx", {"path": "book.xlsx", "sheet": "Data", "append_rows": [["Bob", 7]]})
    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "Bob, 7" in result


@pytest.mark.asyncio
async def test_edit_xlsx_set_cells(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch(
        "create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["a", "b"], ["c", "d"]]}]}
    )

    await registry.dispatch("edit_xlsx", {"path": "book.xlsx", "sheet": "Data", "set_cells": [{"cell": "B2", "value": "changed"}]})
    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "c, changed" in result


@pytest.mark.asyncio
async def test_edit_xlsx_no_changes_when_nothing_to_do(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": []}]})

    result = await registry.dispatch("edit_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "No changes made" in result


@pytest.mark.asyncio
async def test_edit_xlsx_missing_file(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="File not found"):
        await registry.dispatch("edit_xlsx", {"path": "missing.xlsx", "sheet": "Data", "append_rows": [["a"]]})


@pytest.mark.asyncio
async def test_edit_xlsx_unknown_sheet(tmp_path):
    registry, _ = _registry(tmp_path)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": []}]})

    with pytest.raises(ToolExecutionError, match="No sheet named"):
        await registry.dispatch("edit_xlsx", {"path": "book.xlsx", "sheet": "Missing", "append_rows": [["a"]]})


@pytest.mark.asyncio
async def test_read_xlsx_rejects_oversized_file(tmp_path):
    registry, _ = _registry(tmp_path, max_read_bytes=10)
    await registry.dispatch("create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["a", "b"]]}]})

    with pytest.raises(ToolExecutionError, match="over the"):
        await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})


@pytest.mark.asyncio
async def test_read_xlsx_truncates_long_output(tmp_path):
    registry, _ = _registry(tmp_path, max_extract_chars=5)
    await registry.dispatch(
        "create_xlsx", {"path": "book.xlsx", "sheets": [{"name": "Data", "rows": [["a much longer row than the cap"]]}]}
    )

    result = await registry.dispatch("read_xlsx", {"path": "book.xlsx", "sheet": "Data"})

    assert "truncated" in result


@pytest.mark.asyncio
async def test_read_xlsx_blocks_path_traversal(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="outside the sandbox root"):
        await registry.dispatch("read_xlsx", {"path": "../escaped.xlsx"})


@pytest.mark.asyncio
async def test_create_xlsx_rejects_absolute_path(tmp_path):
    registry, _ = _registry(tmp_path)
    absolute_target = tmp_path / "elsewhere.xlsx"

    with pytest.raises(ToolExecutionError, match="Absolute paths are not allowed"):
        await registry.dispatch("create_xlsx", {"path": str(absolute_target), "sheets": [{"name": "S", "rows": []}]})
    assert not absolute_target.exists()
