"""agent_config_writer — persists runtime changes to config/agents.json so
that Agent self-extension (propose_new_agent, see
tools/self_extend/propose_agent_tool.py) and capability grants
(propose_new_skill, propose_new_agent, propose_mcp_server) survive a
restart, not just the in-memory ScopedToolRegistryView/AgentRegistry they
also update immediately.

This closes a real gap found while adopting the user-authored make_pptx
skill: propose_new_skill's grant_access previously only widened the
Leader's in-memory ScopedToolRegistryView, so a newly granted capability
"disappeared" the moment AuraAgent restarted (the tool was still in the
shared ToolRegistry, but no agent's `capabilities` pattern in
config/agents.json matched it). Every self-extension tool must call the
matching function here in addition to its in-memory update.

Mirrors the JSON-file + asyncio.Lock pattern already used by
tools/memory/memory_store.py — the lock guards this one file's
read-modify-write pair against concurrent self-extension proposals, which
Multi-Agent's concurrent tool dispatch (core/react_engine.py) makes a real
possibility rather than a theoretical one.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


def _load(config_path: Path) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _save(config_path: Path, config: dict[str, Any]) -> None:
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


async def add_agent_entry(config_path: Path, agent_dict: dict[str, Any], lock: asyncio.Lock) -> None:
    """Append a new agent entry to config/agents.json's "agents" list."""
    async with lock:
        config = _load(config_path)
        config["agents"].append(agent_dict)
        _save(config_path, config)


async def remove_agent_entry(config_path: Path, agent_name: str, lock: asyncio.Lock) -> None:
    """Remove an agent entry by name. A no-op if the name is absent, so a
    retried removal (or one racing an already-applied one) doesn't raise."""
    async with lock:
        config = _load(config_path)
        config["agents"] = [entry for entry in config["agents"] if entry["name"] != agent_name]
        _save(config_path, config)


async def add_capability(config_path: Path, agent_name: str, pattern: str, lock: asyncio.Lock) -> None:
    """Append one capability pattern to an existing agent's "capabilities"
    list. Idempotent — a pattern already present is left alone, so an
    approval replayed twice (or a skill reinstalled after manual editing)
    doesn't pile up duplicates."""
    async with lock:
        config = _load(config_path)
        for entry in config["agents"]:
            if entry["name"] == agent_name:
                if pattern not in entry["capabilities"]:
                    entry["capabilities"].append(pattern)
                break
        else:
            raise ValueError(f"No agent named '{agent_name}' in '{config_path}'")
        _save(config_path, config)
