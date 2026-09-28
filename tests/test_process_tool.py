"""Tests for tools/system/process_tool.py. list_processes runs against
the REAL process table (read-only, safe). kill_process spawns a
disposable, harmless throwaway subprocess of its own to kill -- it never
touches a real pre-existing process on this machine.
"""
from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from core.exceptions import ToolExecutionError
from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.system.process_tool import register_process_tools


def _registry(decision: bool = True):
    registry = ToolRegistry()
    confirmation = FakeConfirmationChannel(decision=decision)
    register_process_tools(registry, confirmation)
    return registry, confirmation


@pytest.fixture
def disposable_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(0.2)  # give it a moment to actually start
    yield proc
    if psutil.pid_exists(proc.pid):
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_list_processes_finds_this_own_test_run():
    registry, _ = _registry()

    result = await registry.dispatch("list_processes", {"filter": "python"})

    assert str(psutil.Process().pid) in result or "python" in result.lower()


@pytest.mark.asyncio
async def test_list_processes_reports_no_match_clearly():
    registry, _ = _registry()

    result = await registry.dispatch("list_processes", {"filter": "definitely-not-a-real-process-xyz123"})

    assert result == "No matching processes found."


@pytest.mark.asyncio
async def test_kill_process_confirmed_actually_terminates_it(disposable_process):
    registry, confirmation = _registry(decision=True)

    result = await registry.dispatch("kill_process", {"pid": disposable_process.pid})

    assert not psutil.pid_exists(disposable_process.pid)
    assert str(disposable_process.pid) in result
    assert confirmation.requests[0].risk_level == "destructive"


@pytest.mark.asyncio
async def test_kill_process_declined_leaves_it_running(disposable_process):
    registry, _ = _registry(decision=False)

    result = await registry.dispatch("kill_process", {"pid": disposable_process.pid})

    assert psutil.pid_exists(disposable_process.pid)
    assert "declined" in result.lower()


@pytest.mark.asyncio
async def test_kill_process_unknown_pid_rejected_before_confirming():
    registry, confirmation = _registry()

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("kill_process", {"pid": 999_999_999})
    assert confirmation.requests == []
