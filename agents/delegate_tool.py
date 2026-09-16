"""build_delegate_tool — wraps a pre-built Worker AsyncReActEngine into an
ordinary (ToolSpec + handler) pair the Leader can call like any other tool.

This is the entire "worker-as-tool" mechanism: from
core/react_engine.py's perspective, delegating to a worker is
indistinguishable from calling calculate or fetch_url — it's just another
name the ToolRegistry.dispatch() call path (via the Leader's
ScopedToolRegistryView) routes an argument dict to. The engine never
imports this module or anything else from agents/.

The Worker's AsyncReActEngine is built ONCE in main.py and passed in here
already-constructed — never built fresh per call. This is safe (not just
cheaper) because AsyncReActEngine.run() builds its `history` as a local
variable fresh every call and touches no other `self` state, so multiple
concurrent delegate_to_<worker> calls (or separate calls to different
workers) safely share the same pre-built engine instances with no
cross-talk — see core/react_engine.py's concurrent tool dispatch, which is
exactly what makes calling two different workers in one Leader turn run
in parallel rather than queued.
"""
from __future__ import annotations

from typing import Any

from agents.agent_definition import AgentDefinition
from core.react_engine import AsyncReActEngine
from tools.base import RegisteredTool, ToolSpec


def build_delegate_tool(worker: AgentDefinition, worker_engine: AsyncReActEngine) -> RegisteredTool:
    async def handler(args: dict[str, Any]) -> str:
        return await worker_engine.run(args["task"])

    spec = ToolSpec(
        name=f"delegate_to_{worker.name}",
        description=(
            f"Delegate a task to the '{worker.name}' worker agent, which has access to: "
            f"{', '.join(worker.capabilities)}. Describe the task in plain language — the "
            "worker will use its own tools and reasoning to complete it and report back a final answer."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task to hand off, described in plain language."}
            },
            "required": ["task"],
        },
    )
    return RegisteredTool(spec=spec, handler=handler)
