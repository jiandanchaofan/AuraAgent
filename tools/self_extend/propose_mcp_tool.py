"""propose_mcp_server — lets the Leader (orchestrator-only) propose
connecting a brand-new MCP server at runtime. This is the HIGHEST risk
self-extension surface in AuraAgent — higher than propose_new_skill's
LLM-authored code (which a human reads line-by-line before it can run) and
higher than propose_new_agent's scope widening (which only reaches tools
that already exist): approving this spawns an arbitrary subprocess,
chosen by the LLM, with AuraAgent's own permissions, no sandbox, and no
code for a human to actually read beforehand — the human is trusting the
command/package itself, not reviewing its behavior. See
tools/self_extend/propose_skill_tool.py and propose_agent_tool.py for the
lower two tiers of the same "propose -> full human review -> approve or
decline" shape this repeats, and note how much heavier the review text
here is as a result.

The LLM may only name which environment variables the server needs
(`env_keys_needed`, e.g. ["BRAVE_API_KEY"]) — it never supplies values.
Every value is collected directly from the human via
ConfirmationChannel.ask_open_question(), so a secret never passes through
the LLM's context window or gets written to logs/session-*.jsonl.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from mcp_integration.mcp_client_manager import MCPClientManager
from mcp_integration.mcp_config_writer import add_server_entry
from tools.base import ToolSpec
from tools.registry import ToolRegistry

_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def register_propose_mcp_tool(
    registry: ToolRegistry,
    mcp_manager: MCPClientManager,
    mcp_config_path: Path,
    mcp_config_lock: asyncio.Lock,
    confirmation_channel: ConfirmationChannel,
    grant_access: Callable[[str], Awaitable[None]],
) -> None:
    """`grant_access(pattern)` is the same composed callback
    propose_new_skill uses (see main.py) — it both widens the Leader's own
    ScopedToolRegistryView and persists the capability grant to
    config/agents.json. Called here with the pattern `mcp_<name>_*` once
    the new server has actually connected."""

    async def propose_mcp_server(args: dict[str, Any]) -> str:
        name = args["name"]
        command = args["command"]
        server_args = args.get("args", [])
        env_keys_needed = args.get("env_keys_needed", [])
        reason = args.get("reason", "")
        capability_description = args.get("capability_description", "")

        if not _NAME_PATTERN.match(name):
            raise ToolExecutionError(f"Invalid server name '{name}': must match {_NAME_PATTERN.pattern!r}.")
        if name in mcp_manager.connected_servers:
            raise ToolExecutionError(f"An MCP server named '{name}' is already connected.")
        if not isinstance(command, str) or not command.strip():
            raise ToolExecutionError("`command` must be a non-empty string.")
        if not isinstance(server_args, list) or not all(isinstance(a, str) for a in server_args):
            raise ToolExecutionError("`args` must be a list of strings.")
        if not isinstance(env_keys_needed, list) or not all(isinstance(k, str) for k in env_keys_needed):
            raise ToolExecutionError("`env_keys_needed` must be a list of strings.")

        proposal = _build_proposal_text(name, command, server_args, env_keys_needed, reason, capability_description)

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_mcp_server",
                arguments={"name": name, "command": command},
                reason=proposal,
                risk_level="arbitrary_execution",
            )
        )
        if not approved:
            return f"User declined to connect the new MCP server '{name}'. No changes were made."

        env: dict[str, str] = {}
        for key in env_keys_needed:
            value = await confirmation_channel.ask_open_question(
                f"Enter the value for environment variable '{key}', needed by the new MCP server "
                f"'{name}'. This value is never shown to the AI and never logged."
            )
            env[key] = value

        server_config: dict[str, Any] = {"name": name, "command": command, "args": server_args}
        if env:
            server_config["env"] = env

        await mcp_manager.connect_one(server_config)
        if name not in mcp_manager.connected_servers:
            return (
                f"Failed to connect to the new MCP server '{name}' — check the command/args are "
                "correct. Nothing was saved."
            )

        await add_server_entry(mcp_config_path, server_config, mcp_config_lock)
        await grant_access(f"mcp_{name}_*")

        return (
            f"Connected new MCP server '{name}'. Its tools are now available for the rest of this "
            "session, and the server persists on disk for future sessions too."
        )

    registry.register(_spec(), propose_mcp_server)


def _spec() -> ToolSpec:
    return ToolSpec(
        name="propose_mcp_server",
        description=(
            "Propose connecting a brand-new MCP server when the user needs a capability that no "
            "existing tool, skill, or MCP server provides, and that capability is best served by an "
            "existing MCP server package rather than a new Skill. This is the highest-risk "
            "self-extension tool available: it runs an external command chosen by you with full "
            "system permissions, no sandbox. A human reviews and approves the exact command before "
            "it ever runs. Only name environment variables the server needs by KEY "
            "(env_keys_needed) -- never invent or supply their values, a human will be asked for "
            "those directly."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Identifier for the server (letters/digits/underscore, starts with a letter "
                        "or underscore). Its tools will be namespaced as mcp_<name>_<tool>."
                    ),
                },
                "command": {"type": "string", "description": "The executable to run, e.g. 'npx' or 'uvx'."},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command-line arguments, e.g. ['-y', '@some/mcp-server-package'].",
                },
                "env_keys_needed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names ONLY of environment variables the server needs, e.g. ['BRAVE_API_KEY']. Never values.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why no existing tool/skill/MCP server can do this instead.",
                },
                "capability_description": {
                    "type": "string",
                    "description": "What this server's tools will let the team do.",
                },
            },
            "required": ["name", "command"],
        },
    )


def _build_proposal_text(
    name: str,
    command: str,
    server_args: list[str],
    env_keys_needed: list[str],
    reason: str,
    capability_description: str,
) -> str:
    full_command = " ".join([command, *server_args])
    lines = [f"The AI wants to connect a new MCP server: '{name}'"]
    if capability_description:
        lines.append(f"What it would let the team do: {capability_description}")
    if reason:
        lines.append(f"Why: {reason}")
    lines.append("")
    lines.append(f"Command that will be run: {full_command}")
    if env_keys_needed:
        lines.append(f"Environment variables it needs (you will be asked for the values, not the AI): {', '.join(env_keys_needed)}")
    lines.append("")
    lines.append("THIS IS THE RISKIEST KIND OF APPROVAL IN AURAAGENT:")
    lines.append(f"  - It will start a new subprocess running '{full_command}', with the SAME PERMISSIONS as AuraAgent itself, and NO SANDBOX.")
    lines.append("  - Unlike a proposed Skill, there is no source code here for you to read line-by-line -- you are trusting the command/package itself.")
    lines.append(f"  - If approved, this is saved to config/mcp_servers.json and will run automatically every time AuraAgent starts, from now on.")
    lines.append("  - Only approve this if you genuinely trust the source of this command/package.")
    return "\n".join(lines)
