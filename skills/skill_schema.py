"""Parses a SKILL.md file's YAML front matter into a SkillManifest.

Expected format:
    ---
    name: word_count
    description: ...
    input_schema:
      type: object
      properties: {...}
      required: [...]
    ---
    <markdown body, ignored by the loader — for humans>

`input_schema` is optional in the front matter; defaults to an
empty-object schema (a skill that takes no arguments) when omitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SkillManifestError(Exception):
    """Raised when a SKILL.md file is missing, malformed, or incomplete."""


@dataclass
class SkillManifest:
    name: str
    description: str
    input_schema: dict[str, Any]
    entrypoint: Path  # run.py, resolved relative to the skill's own directory


def parse_skill_manifest(skill_dir: Path) -> SkillManifest:
    manifest_path = skill_dir / "SKILL.md"
    if not manifest_path.is_file():
        raise SkillManifestError(f"No SKILL.md found in '{skill_dir}'")

    text = manifest_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillManifestError(f"'{manifest_path}' must start with a '---' YAML front matter block")

    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillManifestError(f"'{manifest_path}' is missing the closing '---' of its front matter block")

    try:
        front_matter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SkillManifestError(f"Invalid YAML front matter in '{manifest_path}': {exc}") from exc

    try:
        name = front_matter["name"]
        description = front_matter["description"]
    except KeyError as exc:
        raise SkillManifestError(f"'{manifest_path}' front matter is missing required field {exc}") from exc

    input_schema = front_matter.get("input_schema", {"type": "object", "properties": {}})

    entrypoint = skill_dir / "run.py"
    if not entrypoint.is_file():
        raise SkillManifestError(f"Skill '{name}' has no run.py entrypoint at '{entrypoint}'")

    return SkillManifest(name=name, description=description, input_schema=input_schema, entrypoint=entrypoint)
