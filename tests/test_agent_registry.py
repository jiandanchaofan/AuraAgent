"""Tests for AgentDefinition validation and AgentRegistry.load()'s
fail-fast roster rules (exactly one leader, unique names, required
fields), plus a regression test against the real shipped config/agents.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.agent_definition import AgentDefinition, AgentDefinitionError
from agents.agent_registry import AgentRegistry, AgentRegistryError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_AGENTS_CONFIG = PROJECT_ROOT / "config" / "agents.json"


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_the_real_shipped_config():
    registry = AgentRegistry.load(REAL_AGENTS_CONFIG)

    assert registry.leader.name == "orchestrator"
    worker_names = {w.name for w in registry.workers}
    assert worker_names == {"researcher", "scheduler"}
    assert registry.get("scheduler").capabilities == ["*calendar*", "*task*"]


def test_agent_definition_rejects_invalid_name():
    with pytest.raises(AgentDefinitionError):
        AgentDefinition(name="bad name!", role="leader", system_prompt="x", capabilities=["*"])


def test_agent_definition_rejects_empty_capabilities():
    with pytest.raises(AgentDefinitionError):
        AgentDefinition(name="ok_name", role="worker", system_prompt="x", capabilities=[])


def test_agent_definition_rejects_invalid_role():
    with pytest.raises(AgentDefinitionError):
        AgentDefinition(name="ok_name", role="manager", system_prompt="x", capabilities=["*"])  # type: ignore[arg-type]


def test_registry_requires_exactly_one_leader(tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "agents": [
                {"name": "a", "role": "worker", "system_prompt": "x", "capabilities": ["*"]},
                {"name": "b", "role": "worker", "system_prompt": "x", "capabilities": ["*"]},
            ]
        },
    )
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(config_path)


def test_registry_rejects_two_leaders(tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "agents": [
                {"name": "a", "role": "leader", "system_prompt": "x", "capabilities": ["*"]},
                {"name": "b", "role": "leader", "system_prompt": "x", "capabilities": ["*"]},
            ]
        },
    )
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(config_path)


def test_registry_rejects_duplicate_names(tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "agents": [
                {"name": "dup", "role": "leader", "system_prompt": "x", "capabilities": ["*"]},
                {"name": "dup", "role": "worker", "system_prompt": "x", "capabilities": ["*"]},
            ]
        },
    )
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(config_path)


def test_registry_rejects_missing_required_field(tmp_path):
    config_path = _write_config(
        tmp_path, {"agents": [{"name": "a", "role": "leader", "capabilities": ["*"]}]}
    )
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(config_path)


def test_registry_rejects_missing_file(tmp_path):
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(tmp_path / "does_not_exist.json")


def test_registry_rejects_invalid_json(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(path)


def test_get_missing_agent_raises(tmp_path):
    config_path = _write_config(
        tmp_path, {"agents": [{"name": "solo", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]}
    )
    registry = AgentRegistry.load(config_path)
    with pytest.raises(AgentRegistryError):
        registry.get("nonexistent")
