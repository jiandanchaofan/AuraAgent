"""MCPClientManager — reads config/mcp_servers.json and connects to
configured MCP servers over stdio, registering each server's advertised
tools into the shared ToolRegistry via mcp_tool_adapter.adapt_and_register().

A server that fails to start or initialize is logged and skipped rather
than aborting startup entirely — MCP servers are optional, best-effort
integrations; one misconfigured entry in config/mcp_servers.json shouldn't
prevent AuraAgent itself from starting. With an empty "servers" list this
is a fast no-op, same as before this module had a real implementation.

A server config with `"follow_active_directory": true` is expected to
take its allowed directory as a command-line argument containing the
literal placeholder `{directory}` somewhere in `args` (e.g.
`"@modelcontextprotocol/server-filesystem"`'s own directory argument) --
substituted with the real path at connect/reconnect time. This is how
cli/service.py::sync_active_directory() makes an MCP filesystem-style
server follow the currently active Project/Chat directory: the MCP
"Roots" protocol feature would be the natural-looking fit here (letting a
client dynamically tell a server which directories it may access without
restarting it), but it was marked deprecated in the protocol spec
(SEP-2577) not long before this was built -- building new functionality
on a just-deprecated feature would be a bad bet, so this reconnects the
server's own subprocess with a new `args` value instead. The cost is
spawning a new subprocess on each actual directory change (not on every
turn -- sync_directory_following_servers() skips the reconnect whenever
the resolved directory hasn't actually changed since last time).

Each connected server runs inside its OWN dedicated, persistent
asyncio.Task (`_server_loop`), not a single shared "owner task" for every
server. This is a deliberate fix for a real, reproduced bug: anyio's
cancel scopes are tracked as a stack PER TASK, and `stdio_client()`/
`ClientSession()` each open their own internal task group (thus their own
cancel scope) -- when an earlier version of this module ran ALL servers'
connect/close/reconnect through one shared owner task, each server's
AsyncExitStack ended up interleaved on that ONE task's cancel-scope
stack, and closing them in anything other than strict reverse-of-entry
order (which a simple per-server reconnect can't guarantee in general --
whichever server most recently reconnected is not necessarily the most
recently *connected* one) raised
`RuntimeError: Attempted to exit a cancel scope that isn't the current
task's current cancel scope`, reproduced directly via a minimal script
connecting two real servers and closing them. Giving each server its own
task sidesteps the whole problem class: that server's `async with
AsyncExitStack()` is always entered AND exited from the exact same task,
and a reconnect is just that same task looping back to open a fresh one
-- other servers' cancel scopes, living on their own tasks' own stacks,
are never involved at all.
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
        self._server_tasks: dict[str, asyncio.Task] = {}
        self._server_commands: dict[str, asyncio.Queue] = {}
        #: Raw config per connected server, kept so a later reconnect can
        #: rebuild `args` with a new directory without re-reading the
        #: config file (which may have changed on disk since startup).
        self._server_configs: dict[str, dict[str, Any]] = {}
        #: Which qualified tool names (mcp_<server>_<tool>) came from each
        #: server, so reconnect can unregister exactly those before
        #: re-registering against the new session.
        self._server_tool_names: dict[str, list[str]] = {}
        #: The directory a follow_active_directory server is CURRENTLY
        #: connected with -- lets sync_directory_following_servers() skip
        #: a reconnect when the resolved directory hasn't actually changed.
        self._server_directories: dict[str, Path] = {}

    async def connect_all(self, initial_directory: Path | None = None) -> None:
        """`initial_directory` is substituted into any follow_active_directory
        server's `{directory}` placeholder at startup -- core/bootstrap.py
        passes the just-constructed workspace_root's initial value, since
        no Project/Chat context exists yet to resolve a more specific one."""
        config = json.loads(self._config_path.read_text(encoding="utf-8"))
        for server_config in config.get("servers", []):
            await self.connect_one(server_config, directory=initial_directory)

    async def connect_one(self, server_config: dict[str, Any], directory: Path | None = None) -> None:
        """Connect to a single MCP server and register its tools. Public
        (not just an implementation detail of connect_all()) so
        propose_mcp_server (tools/self_extend/propose_mcp_tool.py) can
        connect a freshly approved server immediately, without restarting
        AuraAgent or re-reading the whole config file. Safe to call from
        any asyncio Task -- it only ever starts a brand-new, independent
        Task for this one server (see the module docstring)."""
        name = server_config.get("name", "<unnamed>")
        commands: asyncio.Queue = asyncio.Queue()
        ready: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(self._server_loop(name, server_config, directory, commands, ready))
        try:
            tool_names = await ready
        except Exception as exc:  # noqa: BLE001 - one bad server must not block startup
            print(f"[MCP] Failed to connect to '{name}': {type(exc).__name__}: {exc}")
            return

        self._server_tasks[name] = task
        self._server_commands[name] = commands
        self._server_configs[name] = server_config
        self._server_tool_names[name] = tool_names
        if server_config.get("follow_active_directory") and directory is not None:
            self._server_directories[name] = directory
        if name not in self.connected_servers:
            self.connected_servers.append(name)
        print(f"[MCP] Connected to '{name}': {len(tool_names)} tool(s) registered.")

    async def sync_directory_following_servers(self, directory: Path) -> None:
        """Called by cli/service.py::sync_active_directory() on every
        turn. Reconnects every server configured with
        follow_active_directory=true whose currently-connected directory
        differs from `directory`; a no-op for everything else, and a
        cheap dict-lookup no-op even for a following server when the
        directory hasn't actually changed since the last call."""
        for name, config in list(self._server_configs.items()):
            if not config.get("follow_active_directory"):
                continue
            if self._server_directories.get(name) == directory:
                continue
            await self.reconnect_server(name, directory)

    async def reconnect_server(self, name: str, directory: Path) -> None:
        """Disconnects and reconnects one already-known server with a new
        `{directory}` value, entirely within that server's own dedicated
        Task (see module docstring) -- safe to call from any Task.

        Falls back to a fresh connect_one() if this server isn't
        currently tracked (never connected, or a PRIOR reconnect attempt
        already failed and cleaned up its bookkeeping below) -- without
        this, one transient failure (e.g. two reconnects racing and one
        losing, reproduced live during manual verification) would
        permanently disable directory-following for that server for the
        rest of the process's life, since nothing else ever retries it."""
        task = self._server_tasks.get(name)
        commands = self._server_commands.get(name)
        if task is None or commands is None:
            config = self._server_configs.get(name)
            if config is None:
                print(f"[MCP] Cannot reconnect '{name}': no known config (was it ever connected?).")
                return
            await self.connect_one(config, directory=directory)
            return

        old_tool_names = self._server_tool_names.pop(name, [])
        self._registry.unregister(old_tool_names)

        reply: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
        await commands.put(("reconnect", directory, reply))
        try:
            tool_names = await reply
        except Exception as exc:  # noqa: BLE001 - a failed reconnect must not crash the caller
            # The server's own loop task exits on a failed (re)connect
            # (see _server_loop) -- treat it as fully disconnected rather
            # than leaving stale bookkeeping pointing at a dead task.
            self._server_tasks.pop(name, None)
            self._server_commands.pop(name, None)
            self._server_directories.pop(name, None)
            if name in self.connected_servers:
                self.connected_servers.remove(name)
            print(f"[MCP] Failed to reconnect '{name}': {type(exc).__name__}: {exc}")
            return

        self._server_tool_names[name] = tool_names
        self._server_directories[name] = directory
        print(f"[MCP] Connected to '{name}': {len(tool_names)} tool(s) registered.")

    async def close_all(self) -> None:
        for commands in list(self._server_commands.values()):
            await commands.put(("close", None, None))
        for task in list(self._server_tasks.values()):
            await task
        self._server_tasks.clear()
        self._server_commands.clear()

    @staticmethod
    def _substitute_directory(args: list[str], directory: Path | None) -> list[str]:
        if directory is None:
            return list(args)
        return [arg.replace("{directory}", str(directory)) for arg in args]

    async def _server_loop(
        self,
        name: str,
        config: dict[str, Any],
        directory: Path | None,
        commands: asyncio.Queue,
        ready: asyncio.Future[list[str]],
    ) -> None:
        """Owns one server's entire connection lifecycle -- initial
        connect, any number of reconnects, and eventual close -- all from
        this one Task, so its stdio_client()/ClientSession cancel scopes
        are never entered/exited from anywhere else (see module
        docstring). `ready` is resolved with the registered qualified
        tool names on the FIRST successful connect (or its exception on
        failure); a subsequent "reconnect" command's own reply future
        (`pending_reply`) gets the same treatment for that attempt."""
        pending_reply: asyncio.Future[list[str]] | None = None
        try:
            while True:
                async with AsyncExitStack() as stack:
                    command = config["command"]
                    # A plain "python"/"python3" in config/mcp_servers.json
                    # means "run with the same interpreter AuraAgent itself
                    # is running under" — resolving it via sys.executable
                    # guarantees the child process sees the same venv/
                    # site-packages (e.g. the `mcp` package) rather than
                    # whatever "python" happens to resolve to on PATH.
                    if command in ("python", "python3"):
                        command = sys.executable
                    args = self._substitute_directory(config.get("args", []), directory)
                    params = StdioServerParameters(command=command, args=args, env=config.get("env"))
                    read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                    await session.initialize()

                    tools_result = await session.list_tools()
                    tool_names = [
                        adapt_and_register(mcp_tool, session, self._registry, name)
                        for mcp_tool in tools_result.tools
                    ]

                    if pending_reply is not None:
                        pending_reply.set_result(tool_names)
                        pending_reply = None
                    else:
                        ready.set_result(tool_names)

                    cmd, payload, reply = await commands.get()
                    if cmd == "close":
                        return
                    # cmd == "reconnect": exiting this `async with` block
                    # (next line) closes the CURRENT session/subprocess --
                    # still inside this same Task -- then the `while`
                    # loops back and opens a fresh one with the new
                    # directory.
                    directory = payload
                    pending_reply = reply
        except Exception as exc:  # noqa: BLE001 - reported to whichever future is currently pending
            target = pending_reply if pending_reply is not None else ready
            if not target.done():
                target.set_exception(exc)
