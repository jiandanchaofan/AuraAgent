"""skill_package — safe validation for externally-sourced Skill packages
(a .zip containing SKILL.md + run.py), used by the /skills load and
/skills install CLI commands (cli/commands.py).

Same review bar as propose_new_skill (tools/self_extend/propose_skill_tool.py):
a human must see the FULL code before it is trusted, regardless of whether
the LLM wrote it or it was downloaded/uploaded — external code is not
inherently safer just because a human chose the URL or file path. This
module's only job is getting the two files' text out of a .zip SAFELY
(rejecting zip-slip path traversal and ambiguous archive layouts); the
actual manifest parsing is deliberately left to skills/skill_schema.py's
parse_skill_manifest() rather than duplicated here — the caller writes the
extracted text into a temp directory and parses it the same way
SkillLoader does, so both paths reject the same malformed manifests the
same way.
"""
from __future__ import annotations

import zipfile
from io import BytesIO

_MAX_PACKAGE_BYTES = 5 * 1024 * 1024  # 5MB — a Skill is a couple of small text files


class SkillPackageError(Exception):
    """Raised when a .zip is not a valid, safe Skill package."""


def extract_skill_files(data: bytes) -> tuple[str, str]:
    """Validate a Skill package's raw bytes and return
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
