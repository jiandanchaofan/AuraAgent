"""Process listing/termination (Epic L2). psutil is the de facto
cross-platform process-management library -- shelling out to
tasklist/ps and parsing their output would be far more fragile and
still wouldn't be genuinely cross-platform.

list_processes is read-only and ungated, but always caps how many rows
it returns (default 100): unlike find_capability's internal search (N3,
which stopped filtering because the candidate pool is small and every
miss is a real capability the model can't find), a real machine can
easily have hundreds of processes, most of them irrelevant to whatever
the user actually asked about -- dumping all of them into context by
default would be pure noise, not a missed-match risk. `filter` narrows
it down instead.

kill_process is ALWAYS confirmed (risk_level="destructive", same tier as
delete_file) -- terminating the wrong process can lose unsaved work in
another app or destabilize the system. The confirmation reason includes
the process's own name/cmdline (looked up before asking), not just the
bare PID, so the human reviewing it actually knows what they'd be killing.
"""
from __future__ import annotations

import asyncio
from typing import Any

import psutil

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry

_MAX_LISTED_PROCESSES = 100


def register_process_tools(registry: ToolRegistry, confirmation_channel: ConfirmationChannel) -> None:
    async def list_processes(args: dict[str, Any]) -> str:
        name_filter = (args.get("filter") or "").lower()
        rows = []
        for proc in psutil.process_iter(["pid", "name", "username"]):
            info = proc.info
            name = info.get("name") or ""
            if name_filter and name_filter not in name.lower():
                continue
            rows.append(f"- [{info['pid']}] {name} (user={info.get('username') or '?'})")

        if not rows:
            return "No matching processes found."
        truncated = len(rows) > _MAX_LISTED_PROCESSES
        rows = rows[:_MAX_LISTED_PROCESSES]
        text = "\n".join(rows)
        if truncated:
            text += f"\n... ({len(rows)}+ shown, capped at {_MAX_LISTED_PROCESSES} -- use `filter` to narrow it down)"
        return text

    async def kill_process(args: dict[str, Any]) -> str:
        pid = args["pid"]
        try:
            proc = psutil.Process(pid)
            description = f"{proc.name()} (pid={pid})"
        except psutil.NoSuchProcess:
            raise ToolExecutionError(f"No process with pid={pid} exists.")
        except psutil.AccessDenied as exc:
            raise ToolExecutionError(f"Access denied inspecting pid={pid}: {exc}") from exc

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="kill_process",
                arguments=args,
                reason=f"Terminate process {description}? This cannot be undone and may lose unsaved work.",
                risk_level="destructive",
            )
        )
        if not approved:
            return f"User declined to terminate {description}."

        try:
            proc.terminate()
            # .wait() blocks synchronously until the process actually exits
            # (or the timeout fires) -- run it off the event loop so a slow
            # process to terminate doesn't freeze every other concurrent
            # task (WebSocket messages, other tool calls) for up to 5s.
            await asyncio.to_thread(proc.wait, timeout=5)
        except psutil.NoSuchProcess:
            pass  # already gone -- treat as success
        except psutil.AccessDenied as exc:
            raise ToolExecutionError(f"Access denied terminating {description}: {exc}") from exc
        except psutil.TimeoutExpired:
            proc.kill()
        return f"Terminated {description}."

    registry.register(
        ToolSpec(
            name="list_processes",
            description=(
                "List currently running processes (pid, name, user). Optionally filter by a case-insensitive "
                f"substring of the process name. Capped at {_MAX_LISTED_PROCESSES} rows -- use `filter` on a "
                "busy machine."
            ),
            input_schema={
                "type": "object",
                "properties": {"filter": {"type": "string", "description": "Substring to match against process names."}},
            },
        ),
        list_processes,
    )
    registry.register(
        ToolSpec(
            name="kill_process",
            description=(
                "Terminate a running process by pid. Always asks the user to confirm first -- this is "
                "irreversible and can affect system stability or lose unsaved work in other applications."
            ),
            input_schema={
                "type": "object",
                "properties": {"pid": {"type": "integer", "description": "Process ID to terminate."}},
                "required": ["pid"],
            },
        ),
        kill_process,
    )
