"""AgentDefinition — a single Agent's identity, role, and capabilities, as
declared in config/agents.json. Parsed by agents/agent_registry.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AgentDefinitionError(Exception):
    """Raised when a single agent's own fields are invalid."""


@dataclass(frozen=True)
class AgentDefinition:
    name: str  # must match _NAME_PATTERN — becomes the delegate_to_<name> tool name for workers
    role: Literal["leader", "worker"]
    system_prompt: str
    capabilities: list[str]  # fnmatch patterns against ToolSpec.name; "*" means everything

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise AgentDefinitionError(
                f"Agent name '{self.name}' must match {_NAME_PATTERN.pattern!r} "
                "(it becomes the delegate_to_<name> tool name for workers)"
            )
        if self.role not in ("leader", "worker"):
            raise AgentDefinitionError(f"Agent '{self.name}' has invalid role '{self.role}'")
        if not self.capabilities:
            raise AgentDefinitionError(f"Agent '{self.name}' must declare at least one capability")
