"""SkillLoader — scans ./skills_store for subfolders containing a SKILL.md
manifest and registers each as a callable tool, giving Skills the same
hot-pluggable parity as native and MCP-provided tools (all three converge
on the same ToolRegistry.register()).

v1 status: skills_store/example_skill/ ships as a real, working example
(SKILL.md + run.py) so this loader has something concrete to discover once
implemented — it isn't a placeholder skill.

TODO (next iteration): parse SKILL.md's YAML front matter into a
SkillManifest, build a ToolSpec + a subprocess-based handler
(`python run.py --args-json '...'`, capturing stdout as the Observation)
from it, and call registry.register().
"""
from __future__ import annotations

from pathlib import Path

from tools.registry import ToolRegistry


class SkillLoader:
    def __init__(self, skills_dir: Path, registry: ToolRegistry) -> None:
        self._skills_dir = skills_dir
        self._registry = registry

    def scan_and_register(self) -> list[str]:
        if not self._skills_dir.exists():
            return []
        raise NotImplementedError("Skill loading lands in a future iteration — see module docstring.")
