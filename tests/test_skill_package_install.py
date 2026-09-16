"""Tests for skills/skill_package.py's stage_skill_install()/
finalize_skill_install() — the shared review/install pair used by both
/skills load|install (cli/commands.py) and propose_external_skill
(tools/self_extend/propose_external_skill_tool.py), independent of
extract_skill_files() (see test_skill_package.py) since propose_external_skill
never goes through a zip at all.
"""
from __future__ import annotations

import pytest

from skills.skill_loader import SkillLoader
from skills.skill_package import SkillPackageError, finalize_skill_install, stage_skill_install
from skills.skill_schema import SkillManifestError
from tools.registry import ToolRegistry

_SKILL_MD = "---\nname: demo_skill\ndescription: a demo\n---\nbody\n"
_RUN_PY = (
    "import argparse\n"
    "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
    "p.parse_args()\n"
    "print('ok')\n"
)


def _setup(tmp_path):
    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    registry = ToolRegistry()
    skill_loader = SkillLoader(skills_dir, registry)
    return skills_dir, registry, skill_loader


def test_stage_returns_manifest_and_content_without_touching_disk(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)

    staged = stage_skill_install(_SKILL_MD, _RUN_PY, skills_dir, registry)

    assert staged.manifest.name == "demo_skill"
    assert staged.skill_md_text == _SKILL_MD
    assert staged.run_py_text == _RUN_PY
    assert staged.warnings == []
    assert not staged.skill_dir.exists()  # nothing written yet
    assert not (skills_dir / "demo_skill").exists()


def test_stage_rejects_bad_manifest(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)

    with pytest.raises(SkillManifestError):
        stage_skill_install("no front matter", _RUN_PY, skills_dir, registry)


def test_stage_rejects_invalid_skill_name(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)
    bad_md = "---\nname: not a valid name!\ndescription: x\n---\nbody\n"

    with pytest.raises(SkillPackageError):
        stage_skill_install(bad_md, _RUN_PY, skills_dir, registry)


def test_stage_rejects_existing_skill_directory(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)
    (skills_dir / "demo_skill").mkdir()

    with pytest.raises(SkillPackageError):
        stage_skill_install(_SKILL_MD, _RUN_PY, skills_dir, registry)


def test_stage_rejects_existing_tool_name_collision(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)

    async def _handler(args):
        return "x"

    from tools.base import ToolSpec

    registry.register(
        ToolSpec(name="demo_skill", description="already here", input_schema={"type": "object", "properties": {}}),
        _handler,
    )

    with pytest.raises(SkillPackageError):
        stage_skill_install(_SKILL_MD, _RUN_PY, skills_dir, registry)


def test_stage_surfaces_risky_pattern_warnings(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)
    risky_code = "import subprocess\nsubprocess.run(['ls'])\n"

    staged = stage_skill_install(_SKILL_MD, risky_code, skills_dir, registry)

    assert any("subprocess" in w for w in staged.warnings)


@pytest.mark.asyncio
async def test_finalize_writes_files_and_hot_registers(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)
    staged = stage_skill_install(_SKILL_MD, _RUN_PY, skills_dir, registry)

    registered_name = finalize_skill_install(staged, skill_loader)

    assert registered_name == "demo_skill"
    assert (skills_dir / "demo_skill" / "run.py").read_text(encoding="utf-8") == _RUN_PY
    result = await registry.dispatch("demo_skill", {})
    assert result == "ok"


def test_finalize_rolls_back_directory_on_registration_failure(tmp_path):
    skills_dir, registry, skill_loader = _setup(tmp_path)
    staged = stage_skill_install(_SKILL_MD, _RUN_PY, skills_dir, registry)

    class FailingLoader:
        def register_one(self, skill_dir):
            raise ValueError("simulated registration failure")

    with pytest.raises(ValueError):
        finalize_skill_install(staged, FailingLoader())

    assert not staged.skill_dir.exists()
