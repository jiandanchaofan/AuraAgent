"""propose_new_agent — lets the Leader (only orchestrator has this
capability, see config/agents.json) propose adding a brand-new Worker
Agent to the team at runtime, mirroring propose_new_skill's
"structural pre-check -> full human review -> approve/decline ->
hot-register + persist" shape (see
tools/self_extend/propose_skill_tool.py's module docstring for why that
shape exists).

Risk tier is lower than propose_new_skill: a new Agent can only ever
reach tools that ALREADY exist in the shared ToolRegistry, via
`capabilities` fnmatch patterns — there is no new code execution here, so
`risk_level="scope_expansion"` rather than propose_new_skill's
"code_execution". It is still a real capability change worth a human's
attention: a new Agent widens the Leader's own reach (a new
delegate_to_<name> tool it can call), and an overly broad `capabilities`
list (e.g. "*") would hand that new Agent access to everything. The human
review therefore shows, for every proposed capability pattern, which real
tools in the shared registry it would actually match — not just the
pattern text — so a typo or an over-broad glob is visible at a glance
rather than requiring the reviewer to mentally re-derive fnmatch semantics.
"""
from __future__ import annotations

import asyncio
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from agents.agent_builder import build_worker, ensure_worker_name_available
from agents.agent_config_writer import add_agent_entry
from agents.agent_definition import AgentDefinition, AgentDefinitionError
from agents.agent_registry import AgentRegistry
from agents.scoped_tool_registry import ScopedToolRegistryView
from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from core.logger import AuraLogger
from providers.base import LLMProvider
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def register_propose_agent_tool(
    registry: ToolRegistry,
    agent_registry: AgentRegistry,
    leader_view: ScopedToolRegistryView,
    provider: LLMProvider,
    logger: AuraLogger,
    max_turns: int,
    agents_config_path: Path,
    agents_config_lock: asyncio.Lock,
    confirmation_channel: ConfirmationChannel,
) -> None:
    async def propose_new_agent(args: dict[str, Any]) -> str:
        name = args["name"]
        system_prompt = args["system_prompt"]
        capabilities = args["capabilities"]
        reason = args.get("reason", "")

        try:
            # role is fixed to "worker" here, never taken from `args` — an
            # LLM-proposed agent must never be able to create a second,
            # equally-privileged leader for itself to escalate into.
            new_agent = AgentDefinition(name=name, role="worker", system_prompt=system_prompt, capabilities=capabilities)
        except AgentDefinitionError as exc:
            raise ToolExecutionError(str(exc)) from exc

        try:
            ensure_worker_name_available(name, agent_registry, registry)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc

        proposal = _build_proposal_text(new_agent, reason, registry)

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_new_agent",
                arguments={"name": name},
                reason=proposal,
                risk_level="scope_expansion",
            )
        )
        if not approved:
            return f"User declined to add the new agent '{name}'. No changes were made."

        delegate_tool = build_worker(new_agent, registry, provider, logger, max_turns)
        leader_view.add_extra_tool(delegate_tool)
        agent_registry.add_worker(new_agent)
        await add_agent_entry(
            agents_config_path,
            {"name": name, "role": "worker", "system_prompt": system_prompt, "capabilities": capabilities},
            agents_config_lock,
        )

        return (
            f"Added new agent '{name}' to the team. delegate_to_{name} is now available for the "
            "rest of this session, and persists on disk for future sessions too."
        )

    registry.register(_spec(), propose_new_agent)


def _spec() -> ToolSpec:
    return ToolSpec(
        name="propose_new_agent",
        description=(
            "Propose adding a brand-new worker Agent to the team when the existing team members' "
            "capabilities don't cover something the user needs on an ongoing basis (a one-off task "
            "usually doesn't need a new team member — consider delegating to an existing worker or "
            "using an existing tool first). A human must review the full definition and approve it "
            "before the agent is created. The new agent can only ever use tools that already exist "
            "(no new code is written), scoped by the `capabilities` patterns you choose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Identifier for the new agent (letters/digits/underscore, starts with a "
                        "letter or underscore). Becomes the delegate_to_<name> tool name."
                    ),
                },
                "system_prompt": {
                    "type": "string",
                    "description": "The new agent's system prompt — its role, focus, and tone.",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "fnmatch patterns against existing tool names this agent may use, e.g. "
                        "['fetch_url', 'calculate'] or ['*task*']. Keep this as narrow as the role "
                        "actually needs."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why an existing worker or tool combination can't cover this need.",
                },
            },
            "required": ["name", "system_prompt", "capabilities"],
        },
    )


def _build_proposal_text(agent: AgentDefinition, reason: str, registry: ToolRegistry) -> str:
    lines = [f"The AI wants to add a new team member: '{agent.name}' (role=worker)"]
    if reason:
        lines.append(f"Why: {reason}")
    lines.append("")
    lines.append("--- System prompt ---")
    lines.append(agent.system_prompt)
    lines.append("--- End of system prompt ---")
    lines.append("")
    lines.append("Requested capabilities and what they actually resolve to:")
    all_tool_names = [spec.name for spec in registry.get_tool_specs()]
    for pattern in agent.capabilities:
        matches = sorted(name for name in all_tool_names if fnmatch(name, pattern))
        if matches:
            lines.append(f"  - '{pattern}' -> {', '.join(matches)}")
        else:
            lines.append(f"  - '{pattern}' -> (matches no currently-registered tool)")
    lines.append("")
    lines.append(
        f"This creates a new tool 'delegate_to_{agent.name}' visible ONLY to the orchestrator, and "
        "persists this agent to config/agents.json so it is still here after a restart."
    )
    return "\n".join(lines)
