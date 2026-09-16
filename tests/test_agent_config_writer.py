"""Tests for agent_config_writer — the read-modify-write-under-lock helpers
that make Agent/Skill capability grants survive a restart (closing the gap
propose_new_skill originally had, see agents/agent_config_writer.py's
module docstring)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.agent_config_writer import add_agent_entry, add_capability, remove_agent_entry


def _write_config(tmp_path: Path, agents: list[dict]) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_add_agent_entry_appends_and_persists(tmp_path):
    config_path = _write_config(tmp_path, [{"name": "orchestrator", "role": "leader",
                                             "system_prompt": "x", "capabilities": ["*"]}])
    lock = asyncio.Lock()

    await add_agent_entry(
        config_path,
        {"name": "analyst", "role": "worker", "system_prompt": "y", "capabilities": ["calculate"]},
        lock,
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    names = {a["name"] for a in saved["agents"]}
    assert names == {"orchestrator", "analyst"}


@pytest.mark.asyncio
async def test_remove_agent_entry_removes_the_named_entry(tmp_path):
    config_path = _write_config(
        tmp_path,
        [
            {"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]},
            {"name": "scheduler", "role": "worker", "system_prompt": "y", "capabilities": ["*task*"]},
        ],
    )
    lock = asyncio.Lock()

    await remove_agent_entry(config_path, "scheduler", lock)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert [a["name"] for a in saved["agents"]] == ["orchestrator"]


@pytest.mark.asyncio
async def test_remove_agent_entry_is_a_no_op_for_unknown_name(tmp_path):
    config_path = _write_config(
        tmp_path, [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]
    )
    lock = asyncio.Lock()

    await remove_agent_entry(config_path, "nonexistent", lock)  # must not raise

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert [a["name"] for a in saved["agents"]] == ["orchestrator"]


@pytest.mark.asyncio
async def test_add_capability_appends_a_pattern_to_the_named_agent(tmp_path):
    config_path = _write_config(
        tmp_path, [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["calculate"]}]
    )
    lock = asyncio.Lock()

    await add_capability(config_path, "orchestrator", "make_pptx", lock)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["agents"][0]["capabilities"] == ["calculate", "make_pptx"]


@pytest.mark.asyncio
async def test_add_capability_is_idempotent(tmp_path):
    config_path = _write_config(
        tmp_path, [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["make_pptx"]}]
    )
    lock = asyncio.Lock()

    await add_capability(config_path, "orchestrator", "make_pptx", lock)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["agents"][0]["capabilities"] == ["make_pptx"]


@pytest.mark.asyncio
async def test_add_capability_raises_for_unknown_agent(tmp_path):
    config_path = _write_config(
        tmp_path, [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]
    )
    lock = asyncio.Lock()

    with pytest.raises(ValueError):
        await add_capability(config_path, "nonexistent", "make_pptx", lock)


@pytest.mark.asyncio
async def test_concurrent_add_agent_entry_calls_do_not_clobber_each_other(tmp_path):
    """The whole point of the shared asyncio.Lock: two self-extension
    proposals approved back-to-back (or truly concurrently, via
    asyncio.gather from Multi-Agent's concurrent tool dispatch) must both
    end up persisted, not one overwriting the other's read-modify-write."""
    config_path = _write_config(
        tmp_path, [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]
    )
    lock = asyncio.Lock()

    await asyncio.gather(
        add_agent_entry(
            config_path,
            {"name": "worker_a", "role": "worker", "system_prompt": "a", "capabilities": ["calculate"]},
            lock,
        ),
        add_agent_entry(
            config_path,
            {"name": "worker_b", "role": "worker", "system_prompt": "b", "capabilities": ["calculate"]},
            lock,
        ),
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    names = {a["name"] for a in saved["agents"]}
    assert names == {"orchestrator", "worker_a", "worker_b"}
