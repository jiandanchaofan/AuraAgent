"""skill_package — safe handling of externally-sourced Skill content (not
LLM-generated), shared by every install path that isn't propose_new_skill:
the /skills load|install CLI commands (cli/commands.py) and the
propose_external_skill self-extension tool
(tools/self_extend/propose_external_skill_tool.py).

Same review bar as propose_new_skill (tools/self_extend/propose_skill_tool.py):
a human must see the FULL code before it is trusted, regardless of whether
the LLM wrote it or it was downloaded/uploaded/found via a marketplace
search — external code is not inherently safer just because a human (or
an LLM acting on a human's intent) chose the source.

Two independent pieces live here:
1. extract_skill_files() — get (skill_md_text, run_py_text) safely out of
   a .zip (rejecting zip-slip path traversal and ambiguous archive
   layouts). Only /skills load|install need this — propose_external_skill
   fetches the two files directly from GitHub's raw content API instead
   (SkillsMP search results point at a GitHub tree URL, not a zip), so it
   skips this step entirely.
2. stage_skill_install() / finalize_skill_install() — the shared "validate
   manifest + structurally pre-check for collisions" / "write to disk +
   hot-register" pair every install path uses ONCE it already has
   (skill_md_text, run_py_text) as plain strings, regardless of where they
   came from. Deliberately split from "how do I get human approval" (a
   plain input() Y/N for the CLI, ConfirmationChannel.confirm() for
   propose_external_skill) — see stage_skill_install()'s docstring.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from skills.skill_loader import SkillLoader
from skills.skill_schema import SkillManifest, SkillManifestError, parse_skill_manifest
from tools.registry import ToolRegistry
from tools.self_extend.code_review import scan_for_risky_patterns

_MAX_PACKAGE_BYTES = 5 * 1024 * 1024  # 5MB — a Skill is a couple of small text files
# Same identifier rule propose_new_skill (tools/self_extend/propose_skill_tool.py)
# enforces for a skill name — defined locally rather than imported since
# it's a small, independent rule each self-extension surface owns.
_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SkillPackageError(Exception):
    """Raised when a .zip is not a valid, safe Skill package, or when
    already-extracted Skill content fails structural pre-checks (bad
    manifest, name collision) before a human is ever asked to review it."""


def extract_skill_files(data: bytes) -> tuple[str, str]:
    """Validate a Skill package's raw zip bytes and return
    (skill_md_text, run_py_text). Accepts SKILL.md/run.py either at the
    zip's root or nested one level inside a single top-level directory
    (the shape a plain "Download ZIP" button typically produces)."""
    if len(data) > _MAX_PACKAGE_BYTES:
        raise SkillPackageError(f"Package is {len(data)} bytes, over the {_MAX_PACKAGE_BYTES}-byte limit.")

    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillPackageError(f"Not a valid zip file: {exc}") from exc

    all_names = zf.namelist()
    _reject_unsafe_paths(all_names)
    file_names = [n for n in all_names if not n.endswith("/")]

    found = _find_at_prefix(file_names, "")
    if found is None:
        top_level_dirs = {n.split("/", 1)[0] for n in file_names if "/" in n}
        if len(top_level_dirs) == 1:
            found = _find_at_prefix(file_names, f"{next(iter(top_level_dirs))}/")
    if found is None:
        raise SkillPackageError(
            "Package must contain SKILL.md and run.py, either at the root or inside a single top-level folder."
        )

    skill_md_name, run_py_name = found
    return (
        zf.read(skill_md_name).decode("utf-8", errors="replace"),
        zf.read(run_py_name).decode("utf-8", errors="replace"),
    )


def _find_at_prefix(file_names: list[str], prefix: str) -> tuple[str, str] | None:
    skill_md_name = f"{prefix}SKILL.md"
    run_py_name = f"{prefix}run.py"
    if skill_md_name in file_names and run_py_name in file_names:
        return skill_md_name, run_py_name
    return None


def _reject_unsafe_paths(names: list[str]) -> None:
    for name in names:
        if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
            raise SkillPackageError(f"Unsafe absolute path in package: '{name}'")
        parts = name.replace("\\", "/").split("/")
        if ".." in parts:
            raise SkillPackageError(f"Unsafe path traversal in package: '{name}'")


@dataclass
class StagedSkillInstall:
    """A Skill that has passed every structural pre-check and is ready to
    show a human for approval — nothing has touched disk yet."""

    manifest: SkillManifest
    skill_md_text: str
    run_py_text: str
    warnings: list[str]
    skill_dir: Path  # target install directory; does not exist yet


def stage_skill_install(skill_md_text: str, run_py_text: str, skills_dir: Path, registry: ToolRegistry) -> StagedSkillInstall:
    """Validate a Skill's manifest+code text and structurally pre-check it
    for collisions — invalid name, an existing skill directory, an
    existing tool name — WITHOUT writing anything to skills_dir and
    WITHOUT asking any human. Raises SkillManifestError (bad/missing
    manifest fields) or SkillPackageError (everything else) so the caller
    can reject before bothering a human, same principle as
    propose_new_skill's pre-checks.

    Deliberately stops here rather than also handling human approval:
    /skills load|install (cli/commands.py) asks via a plain input() Y/N,
    propose_external_skill (tools/self_extend/) asks via
    ConfirmationChannel.confirm() — two different "how do I ask" concerns
    that both want the exact same "is this even installable" answer first.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "SKILL.md").write_text(skill_md_text, encoding="utf-8")
        (tmp_path / "run.py").write_text(run_py_text, encoding="utf-8")
        manifest = parse_skill_manifest(tmp_path)  # raises SkillManifestError

    if not _SKILL_NAME_PATTERN.match(manifest.name):
        raise SkillPackageError(f"Invalid skill name '{manifest.name}' in SKILL.md: must match {_SKILL_NAME_PATTERN.pattern!r}.")

    skill_dir = skills_dir / manifest.name
    if skill_dir.exists():
        raise SkillPackageError(f"A skill named '{manifest.name}' already exists.")

    existing_tool_names = {spec.name for spec in registry.get_tool_specs()}
    if manifest.name in existing_tool_names:
        raise SkillPackageError(f"Tool name '{manifest.name}' is already registered — rejecting.")

    warnings = scan_for_risky_patterns(run_py_text)
    return StagedSkillInstall(
        manifest=manifest, skill_md_text=skill_md_text, run_py_text=run_py_text, warnings=warnings, skill_dir=skill_dir
    )


def finalize_skill_install(staged: StagedSkillInstall, skill_loader: SkillLoader) -> str:
    """After a human has approved `staged`: write SKILL.md/run.py to disk
    and hot-register via SkillLoader.register_one(). Returns the
    registered tool name. Rolls back (removes the just-created directory)
    on a registration failure, mirroring propose_new_skill's rollback."""
    staged.skill_dir.mkdir(parents=True)
    (staged.skill_dir / "SKILL.md").write_text(staged.skill_md_text, encoding="utf-8")
    (staged.skill_dir / "run.py").write_text(staged.run_py_text, encoding="utf-8")

    try:
        return skill_loader.register_one(staged.skill_dir)
    except (SkillManifestError, ValueError):
        shutil.rmtree(staged.skill_dir)
        raise
