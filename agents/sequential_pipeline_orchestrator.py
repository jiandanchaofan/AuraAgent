"""SequentialPipelineOrchestrator — future OrchestrationMode.

Intended behavior: run every agent declared in config/agents.json in
declared order, piping each agent's final answer in as the next agent's
task string (strict A -> B -> C hand-off, no branching, no re-entry).
Unlike Leader-Worker, this doesn't need a delegate_to_<name> tool per
agent — the orchestrator itself drives the sequence directly.

Not implemented yet — this class exists so the OrchestrationMode seam is
real (constructible, discoverable) rather than only described in prose.
"""
from __future__ import annotations

from agents.agent_registry import AgentRegistry
from agents.orchestration_mode import OrchestrationMode


class SequentialPipelineOrchestrator(OrchestrationMode):
    def __init__(self, agent_registry: AgentRegistry) -> None:
        self._agent_registry = agent_registry

    async def run(self, user_input: str) -> str:
        raise NotImplementedError(
            "SequentialPipelineOrchestrator is not implemented yet — see module docstring."
        )
