"""mcp_config_writer — persists runtime changes to config/mcp_servers.json,
the MCP-server counterpart to agents/agent_config_writer.py. Same
JSON-file + asyncio.Lock pattern, kept as a separate module (not shared
code) because the two config files have different list keys ("agents" vs
"servers") and are locked independently — propose_new_agent and
propose_mcp_server proposals can be approved concurrently by design (see
core/react_engine.py's concurrent tool dispatch), and each should only
ever block on writers of its own file.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


async def add_server_entry(config_path: Path, server_dict: dict[str, Any], lock: asyncio.Lock) -> None:
    """Append a new MCP server entry to config/mcp_servers.json's
    "servers" list."""
    async with lock:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["servers"].append(server_dict)
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
