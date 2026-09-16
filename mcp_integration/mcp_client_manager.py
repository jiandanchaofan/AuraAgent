"""MCPClientManager — reads config/mcp_servers.json and connects to
configured MCP servers over stdio, registering each server's advertised
tools into the shared ToolRegistry via mcp_tool_adapter.adapt_and_register().

A server that fails to start or initialize is logged and skipped rather
than aborting startup entirely — MCP servers are optional, best-effort
integrations; one misconfigured entry in config/mcp_servers.json shouldn't
prevent AuraAgent itself from starting. With an empty "servers" list this
is a fast no-op, same as before this module had a real implementation.

All actual stdio/session lifecycle management (entering and exiting the
anyio task groups stdio_client()/ClientSession use internally, via one
shared AsyncExitStack) happens inside a single, persistent "owner"
asyncio.Task — regardless of which task calls connect_one() or close_all().
This was NOT true of an earlier version of this module and caused a real,
reproduced bug: anyio's cancel scopes must be entered and exited from the
exact same Task, but connect_one() can be invoked from a completely
different, short-lived Task than the one that will eventually call
close_all() — e.g. a self-extension tool call (propose_mcp_server)
dispatched concurrently by core/react_engine.py's asyncio.gather, whose
Task has already finished long before AuraAgent shuts down. Routing every
connect/close through the owner task via an asyncio.Queue sidesteps this
entirely: connect_one() and close_all(), called from ANY task, just post a
request and await a Future the owner task resolves.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_integration.mcp_tool_adapter import adapt_and_register
from tools.registry import ToolRegistry


class MCPClientManager:
    def __init__(self, config_path: Path, registry: ToolRegistry) -> None:
        self._config_path = config_path
        self._registry = registry
        self.connected_servers: list[str] = []
        self._exit_stack = AsyncExitStack()
        self._command_queue: asyncio.Queue[tuple[str, Any, asyncio.Future]] = asyncio.Queue()
        self._owner_task: asyncio.Task | None = None

    async def connect_all(self) -> None:
        config = json.loads(self._config_path.read_text(encoding="utf-8"))
        for server_config in config.get("servers", []):
            await self.connect_one(server_config)

    async def connect_one(self, server_config: dict[str, Any]) -> None:
        """Connect to a single MCP server and register its tools. Public
        (not just an implementation detail of connect_all()) so
        propose_mcp_server (tools/self_extend/propose_mcp_tool.py) can
        connect a freshly approved server immediately, without restarting
        AuraAgent or re-reading the whole config file. Safe to call from
        any asyncio Task — see the module docstring for why the actual
        connect work always runs inside the dedicated owner task instead."""
        self._ensure_owner_task()
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(("connect", server_config, future))
        await future

    async def close_all(self) -> None:
        if self._owner_task is None:
            return  # connect_one() was never called — no owner task exists to shut down
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(("shutdown", None, future))
        await future
        await self._owner_task

    def _ensure_owner_task(self) -> None:
        # Synchronous, with no `await` inside — so this is atomic within
        # the single-threaded event loop even if several tasks call
        # connect_one() "concurrently" (e.g. via asyncio.gather); there is
        # no yield point between the None-check and the assignment for a
        # second caller to race into.
        if self._owner_task is None:
            self._owner_task = asyncio.ensure_future(self._owner_loop())

    async def _owner_loop(self) -> None:
        async with self._exit_stack:
            while True:
                command, payload, future = await self._command_queue.get()
                if command == "shutdown":
                    future.set_result(None)
                    return
                await self._do_connect(payload)
                future.set_result(None)

    async def _do_connect(self, server_config: dict[str, Any]) -> None:
        server_name = server_config.get("name", "<unnamed>")
        try:
            command = server_config["command"]
            # A plain "python"/"python3" in config/mcp_servers.json means
            # "run with the same interpreter AuraAgent itself is running
            # under" — resolving it via sys.executable guarantees the child
            # process sees the same venv/site-packages (e.g. the `mcp`
            # package) rather than whatever "python" happens to resolve to
            # on PATH, which may be a different, unrelated installation.
            if command in ("python", "python3"):
                command = sys.executable
            params = StdioServerParameters(
                command=command,
                args=server_config.get("args", []),
                env=server_config.get("env"),
            )
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            for mcp_tool in tools_result.tools:
                adapt_and_register(mcp_tool, session, self._registry, server_name)

            self.connected_servers.append(server_name)
            print(f"[MCP] Connected to '{server_name}': {len(tools_result.tools)} tool(s) registered.")
        except Exception as exc:  # noqa: BLE001 - one bad server must not block startup
            print(f"[MCP] Failed to connect to '{server_name}': {type(exc).__name__}: {exc}")
