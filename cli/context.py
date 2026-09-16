"""CLIContext — bundles the objects cli/commands.py's slash-command
handlers need, all of which main.py already builds for the REPL/engines
anyway. A plain dataclass rather than passing a dozen positional
parameters into dispatch_command() — commands genuinely need this much
because /agents add and /skills load|install replay the same
construct + hot-register + persist steps main.py's own startup does.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agents.agent_registry import AgentRegistry
from agents.scoped_tool_registry import ScopedToolRegistryView
from config.settings import Settings
from core.logger import AuraLogger
from mcp_integration.mcp_client_manager import MCPClientManager
from providers.swappable_provider import SwappableProvider
from skills.skill_loader import SkillLoader
from tools.registry import ToolRegistry


@dataclass
class CLIContext:
    settings: Settings
    registry: ToolRegistry
    agent_registry: AgentRegistry
    leader_view: ScopedToolRegistryView
    provider: SwappableProvider
    logger: AuraLogger
    max_turns: int
    skill_loader: SkillLoader
    mcp_manager: MCPClientManager
    http_client: httpx.AsyncClient
    agents_config_lock: asyncio.Lock
    #: {"anthropic": "<key>", "openai": "<key>"} — the source of truth for
    #: which key /config use switches to, kept in sync with .env by
    #: /config set-key. Populated from Settings at startup so a key already
    #: in .env before this process started is usable immediately too.
    known_api_keys: dict[str, str] = field(default_factory=dict)
    #: Where /config set-key writes — a field (not a hardcoded PROJECT_ROOT
    #: constant inside cli/commands.py) so tests can point it at a tmp_path
    #: file instead of ever touching the real .env.
    env_file_path: Path = field(default_factory=lambda: Path(".env"))
