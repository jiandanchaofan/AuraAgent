"""propose_capability_grant — the lightest-weight tier of self-extension:
grant the Leader access to a tool that is ALREADY installed in the shared
ToolRegistry but not in the Leader's own `capabilities`. Typically reached
after find_capability's internal search flags a match as "not yet granted".

Unlike propose_new_skill/propose_new_agent/propose_mcp_server, no new code
and no new external command is involved here — the code being granted
access to was already reviewed once, at the moment it was first installed
(by a human, via propose_new_skill/propose_new_agent/propose_mcp_server/
the CLI, or shipped with the repo). Still gated by human confirmation
though: `capabilities` is the actual enforcement boundary
ScopedToolRegistryView.dispatch() checks, so silently widening it would
let the Leader's reachable surface grow without the human ever seeing it
happen. `risk_level="capability_grant"` -- a fourth, lighter tier below
propose_new_agent's "scope_expansion" -- reflects that lower stakes with a
correspondingly short review (one tool name, not a whole capabilities
list or a block of source code).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from agents.scoped_tool_registry import ScopedToolRegistryView
from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def register_propose_capability_grant_tool(
    registry: ToolRegistry,
    caller_view: ScopedToolRegistryView,
    confirmation_channel: ConfirmationChannel,
    grant_access: Callable[[str], Awaitable[None]],
) -> None:
    """`grant_access(tool_name)` is the same composed callback
    propose_new_skill/propose_mcp_server use (see main.py) — it both
    widens the caller's ScopedToolRegistryView and persists the grant to
    config/agents.json."""

    async def propose_capability_grant(args: dict[str, Any]) -> str:
        tool_name = args["tool_name"]
        reason = args.get("reason", "")

        specs_by_name = {spec.name: spec for spec in registry.get_tool_specs()}
        if tool_name not in specs_by_name:
            raise ToolExecutionError(f"No tool named '{tool_name}' is currently registered.")
        if caller_view.is_allowed(tool_name):
            raise ToolExecutionError(f"'{tool_name}' is already available to you — no grant needed.")

        description = specs_by_name[tool_name].description
        proposal = (
            f"The AI wants to gain access to an already-installed tool: '{tool_name}'\n"
            f"Description: {description}\n"
            + (f"Why: {reason}\n" if reason else "")
            + "\nThis does NOT install any new code -- the tool already exists in the system. "
            "Approving this only widens what the orchestrator is allowed to call."
        )

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_capability_grant",
                arguments={"tool_name": tool_name},
                reason=proposal,
                risk_level="capability_grant",
            )
        )
        if not approved:
            return f"User declined to grant access to '{tool_name}'. No changes were made."

        await grant_access(tool_name)
        return f"Granted access to '{tool_name}'. It is now available for the rest of this session, and persists across restarts."

    registry.register(
        ToolSpec(
            name="propose_capability_grant",
            description=(
                "Request access to a tool that is ALREADY installed on this system but not currently "
                "in your capabilities (find_capability's internal search flags these). Use this instead "
                "of propose_new_skill when the capability already exists -- no new code is written."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Exact name of the already-installed tool."},
                    "reason": {"type": "string", "description": "Why you need this tool."},
                },
                "required": ["tool_name"],
            },
        ),
        propose_capability_grant,
    )
