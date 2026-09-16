"""capability_search — the read-only lookups behind find_capability
(find_capability_tool.py): search the full shared ToolRegistry for an
already-installed match, search the official MCP registry, and search
SkillsMP (a third-party aggregator of public GitHub SKILL.md files).

Each external search function NEVER raises — a network hiccup, timeout, or
unexpected response from one source returns an empty candidate list plus
an error string instead of blowing up the whole find_capability call, so
one bad source never hides the other two. This mirrors the "one bad MCP
server/Skill doesn't block the others" posture already used elsewhere
(mcp_client_manager.py, skill_loader.py).

Real API shapes were verified against the live services (not guessed)
before writing this:
  - MCP registry: GET https://registry.modelcontextprotocol.io/v0/servers?search=...
    Only entries with a `packages` array containing a stdio-transport
    npm/pypi package translate directly into propose_mcp_server's
    command/args/env_keys_needed — "remote" (HTTP/SSE) entries are skipped
    because this codebase's MCPClientManager only implements stdio.
  - SkillsMP: GET https://skillsmp.com/api/v1/skills/search?q=...
    Anonymous tier, no API key. Results point at a GitHub tree URL (a
    subfolder within a larger repo), not a downloadable zip — that's why
    propose_external_skill_tool.py fetches raw file content directly
    rather than reusing skills/skill_package.py's zip extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from agents.scoped_tool_registry import ScopedToolRegistryView
from tools.registry import ToolRegistry

_MCP_REGISTRY_SEARCH_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_SKILLSMP_SEARCH_URL = "https://skillsmp.com/api/v1/skills/search"

# Self-extension tools and delegate wrappers are structural, not something
# a user's "find me a capability for X" intent should ever surface.
_EXCLUDED_INTERNAL_NAMES = {
    "find_capability",
    "propose_capability_grant",
    "propose_new_skill",
    "propose_new_agent",
    "propose_mcp_server",
    "propose_external_skill",
}


@dataclass
class InternalCandidate:
    name: str
    description: str
    already_allowed: bool


@dataclass
class MCPCandidate:
    name: str
    description: str
    command: str
    args: list[str]
    env_keys_needed: list[str] = field(default_factory=list)
    repository_url: str | None = None


@dataclass
class SkillCandidate:
    name: str
    author: str
    description: str
    github_url: str
    stars: int = 0


def search_internal(registry: ToolRegistry, caller_view: ScopedToolRegistryView, intent: str) -> list[InternalCandidate]:
    """Keyword match over EVERY tool in the shared registry (not just the
    caller's own capability-filtered subset) — the whole point is to
    surface something already installed but not yet granted to the caller.
    Plain case-insensitive substring/word overlap, same dependency-light
    style as MemoryStore.search_facts()/notes search — no embeddings."""
    words = [w for w in re.findall(r"[a-z0-9]+", intent.lower()) if len(w) > 2]
    if not words:
        return []

    scored: list[tuple[int, Any]] = []
    for spec in registry.get_tool_specs():
        if spec.name in _EXCLUDED_INTERNAL_NAMES or spec.name.startswith("delegate_to_"):
            continue
        haystack = f"{spec.name} {spec.description}".lower()
        score = sum(1 for word in words if word in haystack)
        if score:
            scored.append((score, spec))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        InternalCandidate(name=spec.name, description=spec.description, already_allowed=caller_view.is_allowed(spec.name))
        for _, spec in scored[:5]
    ]


async def search_mcp_registry(
    http_client: httpx.AsyncClient, intent: str, timeout_seconds: float
) -> tuple[list[MCPCandidate], str | None]:
    try:
        response = await http_client.get(
            _MCP_REGISTRY_SEARCH_URL, params={"search": intent, "limit": 5}, timeout=timeout_seconds
        )
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        return [], "MCP registry search timed out."
    except httpx.HTTPError as exc:
        return [], f"MCP registry search failed: {exc}"
    except ValueError as exc:
        return [], f"MCP registry returned an unexpected response: {exc}"

    candidates = []
    for entry in data.get("servers", []) or []:
        candidate = _mcp_candidate_from_server(entry.get("server") or {})
        if candidate is not None:
            candidates.append(candidate)
    return candidates, None


def _mcp_candidate_from_server(server: dict[str, Any]) -> MCPCandidate | None:
    """Only servers with a stdio-launchable npm/pypi package translate into
    something propose_mcp_server can actually connect (MCPClientManager
    only implements stdio) — HTTP/SSE-only "remote" entries are skipped."""
    for package in server.get("packages", []) or []:
        registry_type = package.get("registryType")
        if (package.get("transport") or {}).get("type") != "stdio":
            continue
        if registry_type == "npm":
            command = package.get("runtimeHint") or "npx"
        elif registry_type == "pypi":
            command = package.get("runtimeHint") or "uvx"
        else:
            continue
        identifier = package.get("identifier")
        if not identifier:
            continue

        args = [
            arg["value"]
            for arg in package.get("runtimeArguments", []) or []
            if arg.get("type") == "positional" and "value" in arg
        ]
        args.append(identifier)
        env_keys_needed = [
            var["name"] for var in package.get("environmentVariables", []) or [] if var.get("isRequired") and var.get("name")
        ]
        return MCPCandidate(
            name=server.get("name", identifier),
            description=server.get("description", ""),
            command=command,
            args=args,
            env_keys_needed=env_keys_needed,
            repository_url=(server.get("repository") or {}).get("url"),
        )
    return None


async def search_skillsmp(
    http_client: httpx.AsyncClient, intent: str, timeout_seconds: float
) -> tuple[list[SkillCandidate], str | None]:
    try:
        response = await http_client.get(_SKILLSMP_SEARCH_URL, params={"q": intent, "limit": 5}, timeout=timeout_seconds)
        if response.status_code == 429:
            return [], "SkillsMP search is rate-limited right now — try again later."
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        return [], "SkillsMP search timed out."
    except httpx.HTTPError as exc:
        return [], f"SkillsMP search failed: {exc}"
    except ValueError as exc:
        return [], f"SkillsMP returned an unexpected response: {exc}"

    skills = ((data.get("data") or {}).get("skills")) or []
    candidates = [
        SkillCandidate(
            name=skill.get("name", "?"),
            author=skill.get("author", "?"),
            description=skill.get("description", ""),
            github_url=skill.get("githubUrl", ""),
            stars=skill.get("stars", 0),
        )
        for skill in skills
    ]
    return candidates, None
