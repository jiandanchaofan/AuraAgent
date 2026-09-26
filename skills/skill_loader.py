"""SkillLoader — scans ./skills_store for subfolders containing a SKILL.md
manifest and registers each as a callable tool, giving Skills the same
hot-pluggable parity as native and MCP-provided tools (all three converge
on the same ToolRegistry.register()).

Invocation convention every skill's run.py must follow: all tool
arguments are passed as a single JSON blob via one `--args-json` flag
(rather than mapping each argument to its own CLI flag) — this keeps the
subprocess-invocation code below identical regardless of what arguments a
particular skill's input_schema declares. The skill's own directory is
used as the subprocess's working directory, so a skill can reference its
own local files with relative paths. The child is launched with
sys.executable (not a bare "python") so it always shares the parent's
Python environment — same fix as mcp_integration/mcp_client_manager.py's.

A skill that fails to parse or load is logged and skipped rather than
aborting startup — same "optional, best-effort" posture as MCP servers.

If `workspace_root` is given, every skill subprocess also gets an
AURA_WORKSPACE_ROOT env var pointing at it — real, live use surfaced a bug
where make_pptx (skills_store/make_pptx/run.py) saved a user-given
`output_path` relative to its OWN directory (this module's documented
`cwd=`, above) rather than the workspace the rest of the app's file tools
(tools/files/file_tool.py) use, so a generated file was both unreachable
by those tools and, for any path with a subdirectory, crashed outright
(python-pptx doesn't create missing parent directories). A skill that
writes user-facing output files is expected to read this env var itself
and resolve/sandbox its own output path against it, the same way
tools/files/file_tool.py resolves against its own workspace_root -- see
make_pptx's run.py for the reference implementation. `workspace_root`
defaults to None (no env var injected) so every existing caller/test that
doesn't care about this keeps its exact prior behavior.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from core.exceptions import ToolExecutionError
from skills.skill_schema import SkillManifest, SkillManifestError, parse_skill_manifest
from tools.base import ToolSpec
from tools.registry import ToolRegistry

DEFAULT_TIMEOUT_SECONDS = 30.0

# Real, reproducible bug this works around: on Windows, a child Python
# process whose stdout is a pipe (not a real console — exactly our case,
# since we capture it with asyncio.subprocess.PIPE) does not default its
# stdout encoding to UTF-8. It falls back to the system ANSI codepage
# (e.g. GBK on a Chinese-locale Windows install), so any non-ASCII output
# a skill prints gets encoded as GBK bytes; our `.decode("utf-8", ...)`
# below then can't decode them and every character becomes U+FFFD. This
# silently corrupted Chinese output before this fix — not just a terminal
# rendering issue, the corrupted text was what actually got returned as
# the Observation and written to logs/session-*.jsonl. Setting
# PYTHONIOENCODING forces the child interpreter to use UTF-8 regardless.
_SKILL_SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


class SkillLoader:
    def __init__(
        self,
        skills_dir: Path,
        registry: ToolRegistry,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        workspace_root: Path | None = None,
    ) -> None:
        self._skills_dir = skills_dir
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._subprocess_env = dict(_SKILL_SUBPROCESS_ENV)
        if workspace_root is not None:
            self._subprocess_env["AURA_WORKSPACE_ROOT"] = str(workspace_root)
        # Tracks every skill name registered so far (scan_and_register() at
        # startup, plus any later register_one() call from propose_new_skill
        # or the /skills load|install CLI commands) — the shared
        # ToolRegistry itself doesn't tag which of its tools came from a
        # Skill vs. a native tool vs. MCP, so this is the only place that
        # can answer "what Skills are installed" for the /skills command.
        self.registered_skill_names: list[str] = []

    def scan_and_register(self) -> list[str]:
        if not self._skills_dir.exists():
            return []

        registered: list[str] = []
        for skill_dir in sorted(p for p in self._skills_dir.iterdir() if p.is_dir()):
            try:
                registered.append(self.register_one(skill_dir))
            except SkillManifestError as exc:
                print(f"[Skills] Failed to load skill from '{skill_dir.name}': {exc}")
                continue
        return registered

    def register_one(self, skill_dir: Path) -> str:
        """Parse and register exactly one skill directory into the shared
        ToolRegistry. Raises SkillManifestError on a bad manifest — callers
        decide whether to skip-and-continue (scan_and_register, at startup)
        or surface the failure directly (propose_new_skill, mid-conversation
        self-extension — see tools/self_extend/propose_skill_tool.py).
        """
        # Resolved to an absolute path up front: manifest.entrypoint is
        # later passed as a subprocess arg alongside `cwd=entrypoint.parent`
        # — if entrypoint were still relative, the child process would
        # resolve it relative to that same cwd a second time, doubling it.
        manifest = parse_skill_manifest(skill_dir.resolve())
        self._register_skill(manifest)
        self.registered_skill_names.append(manifest.name)
        print(f"[Skills] Registered skill '{manifest.name}' from '{skill_dir.name}'.")
        return manifest.name

    def _register_skill(self, manifest: SkillManifest) -> None:
        async def handler(args: dict[str, Any]) -> str:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(manifest.entrypoint),
                "--args-json",
                json.dumps(args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(manifest.entrypoint.parent),
                env=self._subprocess_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout_seconds)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise ToolExecutionError(f"Skill '{manifest.name}' timed out after {self._timeout_seconds}s.")

            if process.returncode != 0:
                raise ToolExecutionError(
                    f"Skill '{manifest.name}' exited with code {process.returncode}: "
                    f"{stderr.decode('utf-8', errors='replace').strip()}"
                )
            return stdout.decode("utf-8", errors="replace").strip()

        self._registry.register(
            ToolSpec(name=manifest.name, description=manifest.description, input_schema=manifest.input_schema),
            handler,
        )
