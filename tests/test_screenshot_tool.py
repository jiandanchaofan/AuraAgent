"""Tests for tools/system/screenshot_tool.py. Captures the REAL screen on
this machine via mss (no mocking) -- confirmation is faked via
FakeConfirmationChannel, same pattern as tests/test_file_tool.py's
delete_file tests.
"""
from __future__ import annotations

import pytest

from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.system.screenshot_tool import register_screenshot_tools
from tools.workspace_root import SwappableWorkspaceRoot


def _registry(tmp_path, decision: bool = True):
    workspace = tmp_path / "workspace"
    registry = ToolRegistry()
    confirmation = FakeConfirmationChannel(decision=decision)
    register_screenshot_tools(registry, SwappableWorkspaceRoot(workspace), confirmation)
    return registry, workspace, confirmation


@pytest.mark.asyncio
async def test_take_screenshot_saves_a_real_png_into_the_workspace(tmp_path):
    registry, workspace, confirmation = _registry(tmp_path)

    result = await registry.dispatch("take_screenshot", {"filename": "test.png"})

    saved = workspace / "screenshots" / "test.png"
    assert saved.is_file()
    assert saved.stat().st_size > 1000  # a real PNG, not an empty/error stub
    assert "screenshots/test.png" in result
    assert len(confirmation.requests) == 1
    assert confirmation.requests[0].risk_level == "privacy_exposure"


@pytest.mark.asyncio
async def test_default_filename_includes_a_timestamp_and_lands_in_screenshots_subdir(tmp_path):
    registry, workspace, _ = _registry(tmp_path)

    await registry.dispatch("take_screenshot", {})

    files = list((workspace / "screenshots").glob("screenshot-*.png"))
    assert len(files) == 1
    assert files[0].stat().st_size > 1000


@pytest.mark.asyncio
async def test_declined_confirmation_writes_no_file(tmp_path):
    registry, workspace, _ = _registry(tmp_path, decision=False)

    result = await registry.dispatch("take_screenshot", {"filename": "declined.png"})

    assert "declined" in result.lower()
    assert not (workspace / "screenshots" / "declined.png").exists()


@pytest.mark.asyncio
async def test_filename_with_subdirectory_is_respected_verbatim(tmp_path):
    registry, workspace, _ = _registry(tmp_path)

    await registry.dispatch("take_screenshot", {"filename": "custom/nested.png"})

    assert (workspace / "custom" / "nested.png").is_file()
