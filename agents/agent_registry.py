"""AgentRegistry — loads config/agents.json into a validated roster of
AgentDefinitions. Fail-fast at startup (same "raise, don't silently
continue" convention as ToolRegistry.register()/config.settings.load_settings())
— a broken agent roster is a configuration error the user should fix
immediately, unlike an optional MCP server or skill, which are best-effort
and get skipped on failure instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.agent_definition import AgentDefinition, AgentDefinitionError


class AgentRegistryError(Exception):
    """Raised when config/agents.json as a whole is invalid — a missing
    file, bad JSON, a missing field, or a roster-level rule violation
    (not exactly one leader, duplicate names). See AgentDefinitionError
    for a single agent's own field validation."""


class AgentRegistry:
    def __init__(self, agent_list: list[AgentDefinition]) -> None:
        self._agents = {a.name: a for a in agent_list}
        if len(self._agents) != len(agent_list):
            raise AgentRegistryError("config/agents.json has duplicate agent names")

        leaders = [a for a in agent_list if a.role == "leader"]
        if len(leaders) != 1:
            raise AgentRegistryError(
                f"config/agents.json must declare exactly one agent with role='leader', found {len(leaders)}"
            )
        self.leader = leaders[0]
        self.workers = [a for a in agent_list if a.role == "worker"]

    @classmethod
    def load(cls, config_path: Path) -> "AgentRegistry":
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AgentRegistryError(f"Agent config file not found: '{config_path}'") from exc
        except json.JSONDecodeError as exc:
            raise AgentRegistryError(f"Invalid JSON in '{config_path}': {exc}") from exc

        agent_list: list[AgentDefinition] = []
        for entry in raw.get("agents", []):
            try:
                agent_list.append(
                    AgentDefinition(
                        name=entry["name"],
                        role=entry["role"],
                        system_prompt=entry["system_prompt"],
                        capabilities=entry["capabilities"],
                    )
                )
            except KeyError as exc:
                raise AgentRegistryError(f"Agent entry missing required field {exc}: {entry}") from exc
            except AgentDefinitionError as exc:
                raise AgentRegistryError(str(exc)) from exc

        return cls(agent_list)

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise AgentRegistryError(f"No agent named '{name}' in the registry") from exc

    def add_worker(self, agent: AgentDefinition) -> None:
        """Register a new worker at runtime — used by propose_new_agent
        (and, later, the /agents add CLI command) once a proposal is
        approved, so this in-memory roster stays consistent with the
        engines/tools main.py already built for it. Raises rather than
        silently overwriting on a name collision — callers are expected to
        have already checked this (propose_new_agent does, as a
        pre-approval structural check)."""
        if agent.role != "worker":
            raise AgentRegistryError("add_worker() only accepts role='worker' — the leader is fixed at load time")
        if agent.name in self._agents:
            raise AgentRegistryError(f"Agent '{agent.name}' already exists")
        self._agents[agent.name] = agent
        self.workers.append(agent)

    def remove_worker(self, name: str) -> None:
        """Remove a worker at runtime — used by the /agents remove CLI
        command. The leader can never be removed (a team with no leader
        breaks the "exactly one leader" invariant load() enforces)."""
        if name == self.leader.name:
            raise AgentRegistryError("Cannot remove the leader agent")
        if name not in self._agents:
            raise AgentRegistryError(f"No agent named '{name}' in the registry")
        del self._agents[name]
        self.workers = [w for w in self.workers if w.name != name]
