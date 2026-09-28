"""Tests for tools/system/notification_tool.py.

Deliberate exception to this project's "test real, don't mock" default:
plyer.notification.notify() pops a REAL system toast on whatever machine
runs the test. Unlike a clipboard write (restorable) or a screenshot file
(deletable), a fired notification can't be cleaned up after the fact and
would visibly interrupt whoever's using this machine when the suite runs
-- so this monkeypatches plyer.notification.notify instead of calling it
for real.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.registry import ToolRegistry
from tools.system.notification_tool import register_notification_tools


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_notification_tools(registry)
    return registry


@pytest.mark.asyncio
async def test_send_notification_calls_plyer_with_the_given_title_and_message(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tools.system.notification_tool.notification.notify",
        lambda **kwargs: calls.append(kwargs),
    )
    registry = _registry()

    result = await registry.dispatch("send_notification", {"title": "Reminder", "message": "Meeting in 5 minutes"})

    assert len(calls) == 1
    assert calls[0]["title"] == "Reminder"
    assert calls[0]["message"] == "Meeting in 5 minutes"
    assert "Reminder" in result


@pytest.mark.asyncio
async def test_backend_failure_becomes_a_clear_tool_execution_error(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("no notification backend available")

    monkeypatch.setattr("tools.system.notification_tool.notification.notify", _raise)
    registry = _registry()

    with pytest.raises(ToolExecutionError) as exc_info:
        await registry.dispatch("send_notification", {"title": "x", "message": "y"})
    assert "no notification backend available" in str(exc_info.value)
