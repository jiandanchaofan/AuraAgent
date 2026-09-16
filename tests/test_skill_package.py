"""Tests for cli/skill_package.py — safe extraction of externally-sourced
Skill packages, used by the /skills load and /skills install CLI
commands. Real zipfile construction, no mocking."""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from cli.skill_package import SkillPackageError, extract_skill_files, _MAX_PACKAGE_BYTES

_SKILL_MD = "---\nname: demo_skill\ndescription: a demo\n---\nbody\n"
_RUN_PY = "print('hello')\n"


def _make_zip(entries: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extracts_files_at_zip_root():
    data = _make_zip({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY})

    skill_md, run_py = extract_skill_files(data)

    assert skill_md == _SKILL_MD
    assert run_py == _RUN_PY


def test_extracts_files_nested_in_a_single_top_level_folder():
    data = _make_zip({"demo_skill/SKILL.md": _SKILL_MD, "demo_skill/run.py": _RUN_PY})

    skill_md, run_py = extract_skill_files(data)

    assert skill_md == _SKILL_MD
    assert run_py == _RUN_PY


def test_rejects_a_non_zip_file():
    with pytest.raises(SkillPackageError):
        extract_skill_files(b"not a zip file")


def test_rejects_missing_run_py():
    data = _make_zip({"SKILL.md": _SKILL_MD})

    with pytest.raises(SkillPackageError):
        extract_skill_files(data)


def test_rejects_ambiguous_multiple_top_level_folders():
    data = _make_zip(
        {"a/SKILL.md": _SKILL_MD, "a/run.py": _RUN_PY, "b/SKILL.md": _SKILL_MD, "b/run.py": _RUN_PY}
    )

    with pytest.raises(SkillPackageError):
        extract_skill_files(data)


def test_rejects_path_traversal_entries():
    data = _make_zip({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY, "../../evil.py": "print('pwned')"})

    with pytest.raises(SkillPackageError):
        extract_skill_files(data)


def test_rejects_absolute_path_entries():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", _SKILL_MD)
        zf.writestr("run.py", _RUN_PY)
        info = zipfile.ZipInfo("/etc/passwd")
        zf.writestr(info, "root:x:0:0")

    with pytest.raises(SkillPackageError):
        extract_skill_files(buf.getvalue())


def test_rejects_oversized_package():
    huge_content = "x" * (_MAX_PACKAGE_BYTES + 1)
    data = _make_zip({"SKILL.md": _SKILL_MD, "run.py": _RUN_PY, "filler.txt": huge_content})

    with pytest.raises(SkillPackageError):
        extract_skill_files(data)
