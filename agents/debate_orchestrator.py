"""DebateOrchestrator — future OrchestrationMode.

Intended behavior: multiple agents respond in rounds to a shared
transcript (round-robin debate), then a designated arbiter agent
synthesizes a final answer from the full discussion (arbitration as
debate's final step, not a separate mode).

Important, honestly-scoped limitation: this does NOT fall out of the
worker-as-tool mechanism for free the way Leader-Worker does. Worker-as-
tool gives each worker an ISOLATED `history` per delegated task (see
AsyncReActEngine.run()) — debate needs a genuinely SHARED transcript
structure that every participating agent's turns get appended to and can
read, which is real, additional design work, not a thin wrapper like
LeaderWorkerOrchestrator turned out to be.

Not implemented yet — this class exists so the OrchestrationMode seam is
real (constructible, discoverable) rather than only described in prose.
"""
from __future__ import annotations

from agents.agent_registry import AgentRegistry
from agents.orchestration_mode import OrchestrationMode


class DebateOrchestrator(OrchestrationMode):
    def __init__(self, agent_registry: AgentRegistry) -> None:
        self._agent_registry = agent_registry

    async def run(self, user_input: str) -> str:
        raise NotImplementedError("DebateOrchestrator is not implemented yet — see module docstring.")
