"""find_capability — read-only discovery tool for the Leader: given a
natural-language description of what capability is needed, looks in three
places, cheapest/most-trusted first:
  1. the full shared ToolRegistry (things already installed, possibly not
     yet granted to the caller),
  2. the official MCP registry (a stdio-launchable server, installable via
     the EXISTING propose_mcp_server),
  3. SkillsMP, a third-party aggregator of public GitHub SKILL.md files
     (installable via the EXISTING propose_external_skill).

This tool never installs or grants anything and never asks a human — pure
search, so it has no risk_level and doesn't touch ConfirmationChannel at
all. Acting on a candidate is always a SEPARATE, already-gated tool call:
propose_capability_grant (internal match), propose_mcp_server (MCP match,
called with the command/args this tool already resolved), or
propose_external_skill (SkillsMP match). See capability_search.py's module
docstring for why the external searches never raise.
"""
from __future__ import annotations

from typing import Any

import httpx

from agents.scoped_tool_registry import ScopedToolRegistryView
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.capability_search import search_internal, search_mcp_registry, search_skillsmp


def register_find_capability_tool(
    registry: ToolRegistry,
    caller_view: ScopedToolRegistryView,
    http_client: httpx.AsyncClient,
    timeout_seconds: float,
) -> None:
    async def find_capability(args: dict[str, Any]) -> str:
        intent = args["intent"]

        internal = search_internal(registry, caller_view, intent)
        mcp_candidates, mcp_error = await search_mcp_registry(http_client, intent, timeout_seconds)
        skill_candidates, skill_error = await search_skillsmp(http_client, intent, timeout_seconds)

        return _render(intent, internal, mcp_candidates, mcp_error, skill_candidates, skill_error)

    registry.register(
        ToolSpec(
            name="find_capability",
            description=(
                "Search for an existing capability (tool/Skill/MCP server) that could satisfy a need, "
                "before deciding to write new code (propose_new_skill) or ask for something you don't "
                "have a source for. Checks, in order: capabilities already installed on this system "
                "(possibly not yet granted to you -- use propose_capability_grant for those), the "
                "official MCP server registry (use propose_mcp_server with the command/args this "
                "returns), and SkillsMP, a third-party index of public GitHub Skill packages (use "
                "propose_external_skill with the github_url this returns). This tool only searches -- "
                "it never installs or grants anything, and a human still reviews and approves whatever "
                "you decide to install next."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Plain-language description of the capability needed.",
                    }
                },
                "required": ["intent"],
            },
        ),
        find_capability,
    )


def _render(intent, internal, mcp_candidates, mcp_error, skill_candidates, skill_error) -> str:
    lines = [f"Capability search results for: {intent!r}", ""]

    lines.append("Already installed on this system:")
    if internal:
        for c in internal:
            status = "already available to you" if c.already_allowed else "NOT yet granted to you -- use propose_capability_grant"
            lines.append(f"  - {c.name}: {c.description} ({status})")
    else:
        lines.append("  (no match)")

    lines.append("")
    lines.append("Official MCP server registry:")
    if mcp_error:
        lines.append(f"  (search unavailable: {mcp_error})")
    elif mcp_candidates:
        for c in mcp_candidates:
            env_note = f", needs env vars: {', '.join(c.env_keys_needed)}" if c.env_keys_needed else ""
            lines.append(
                f"  - {c.name}: {c.description} -- install via propose_mcp_server(command={c.command!r}, "
                f"args={c.args!r}{env_note})"
            )
    else:
        lines.append("  (no match)")

    lines.append("")
    lines.append(
        "SkillsMP (third-party aggregator of public GitHub Skill packages -- NOT an official source. "
        "Many indexed Skills are instruction-only markdown with no executable run.py, which "
        "AuraAgent's Skill format requires -- propose_external_skill will reject those with a clear "
        "error, so treat these as candidates to try, not guaranteed installs):"
    )
    if skill_error:
        lines.append(f"  (search unavailable: {skill_error})")
    elif skill_candidates:
        for c in skill_candidates:
            lines.append(
                f"  - {c.name} by {c.author} ({c.stars} stars): {c.description} -- install via "
                f"propose_external_skill(url={c.github_url!r})"
            )
    else:
        lines.append("  (no match)")

    return "\n".join(lines)
