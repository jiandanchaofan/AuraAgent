"""LeaderWorkerOrchestrator — the only real OrchestrationMode this
iteration implements. All the actual mechanism (worker-as-tool,
ScopedToolRegistryView, concurrent delegation) lives elsewhere; this class
is deliberately thin — it just forwards to the pre-built Leader engine,
which already has delegate_to_<worker> tools in its own scoped view (see
main.py's composition).
"""
from __future__ import annotations

from agents.orchestration_mode import OrchestrationMode
from core.react_engine import AsyncReActEngine


class LeaderWorkerOrchestrator(OrchestrationMode):
    def __init__(self, leader_engine: AsyncReActEngine) -> None:
        self._leader_engine = leader_engine

    async def run(self, user_input: str) -> str:
        return await self._leader_engine.run(user_input)
