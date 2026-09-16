"""agent_builder — constructs a new Worker's ScopedToolRegistryView +
AsyncReActEngine + delegate_to_<name> tool, the exact same three-step
construction main.py's startup loop already does for every worker in
config/agents.json (see main.py's `for worker in agent_registry.workers`
loop). Factored out here so that adding a worker AT RUNTIME — whether
proposed by the LLM and approved by a human (propose_new_agent) or typed
directly by a human (the planned /agents add CLI command) — goes through
the identical construction path startup uses, instead of two
independently-maintained copies of the same three lines that could drift
apart.

Construction is deliberately separate from registration: this function
only builds objects and returns them, it never mutates the Leader's view
or config/agents.json itself — callers decide when (or whether, pending
approval) to actually wire the result in via
ScopedToolRegistryView.add_extra_tool() and agent_config_writer.add_agent_entry().
"""
from __future__ import annotations

from agents.agent_definition import AgentDefinition
from agents.delegate_tool import build_delegate_tool
from agents.scoped_tool_registry import ScopedToolRegistryView
from core.logger import AuraLogger
from core.react_engine import AsyncReActEngine
from providers.base import LLMProvider
from tools.base import RegisteredTool
from tools.registry import ToolRegistry


def build_worker(
    worker: AgentDefinition,
    registry: ToolRegistry,
    provider: LLMProvider,
    logger: AuraLogger,
    max_turns: int,
) -> RegisteredTool:
    """Build a worker's engine and wrap it into a delegate_to_<name> tool
    ready for a Leader's ScopedToolRegistryView.add_extra_tool(). The
    worker's own AsyncReActEngine is closed over by the returned tool's
    handler, exactly as agents/delegate_tool.py documents — no separate
    handle to it needs to be kept by the caller."""
    worker_view = ScopedToolRegistryView(registry, worker.capabilities)
    worker_engine = AsyncReActEngine(
        provider=provider,
        registry=worker_view,
        logger=logger,
        system_prompt=worker.system_prompt,
        max_turns=max_turns,
        agent_name=worker.name,
    )
    return build_delegate_tool(worker, worker_engine)
