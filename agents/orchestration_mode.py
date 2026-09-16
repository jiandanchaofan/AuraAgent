"""OrchestrationMode — the pluggable seam for how a multi-agent team
processes one user request. LeaderWorkerOrchestrator is the only real
implementation this iteration; see sequential_pipeline_orchestrator.py and
debate_orchestrator.py for future modes named as real-but-stubbed seams,
matching this project's established mcp_integration/ and skills/
NotImplementedError-with-accurate-docstring pattern.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class OrchestrationMode(ABC):
    @abstractmethod
    async def run(self, user_input: str) -> str: ...
