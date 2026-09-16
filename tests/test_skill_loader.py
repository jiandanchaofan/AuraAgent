"""Real integration tests for SkillLoader: spawns actual subprocesses
(no mocking) — both the shipped skills_store/example_skill and small
throwaway skills written to tmp_path for the error-path cases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import ToolExecutionError
from tools.registry import ToolRegistry
from skills.skill_loader import SkillLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SKILLS_STORE = PROJECT_ROOT / "skills_store"


def _write_skill(store_dir: Path, name: str, skill_md: str, run_py: str) -> None:
    skill_dir = store_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "run.py").write_text(run_py, encoding="utf-8")


@pytest.mark.asyncio
async def test_loads_and_calls_the_real_example_skill():
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry)

    registered = loader.scan_and_register()

    assert "word_count" in registered
    result = await registry.dispatch("word_count", {"text": "the quick brown fox jumps"})
    assert result == "5"


def test_broken_skill_is_skipped_without_blocking_others(tmp_path):
    _write_skill(
        tmp_path,
        "broken",
        skill_md="no front matter here\n",
        run_py="print('unused')",
    )
    _write_skill(
        tmp_path,
        "good",
        skill_md="---\nname: good_skill\ndescription: works fine\n---\nbody\n",
        run_py="import argparse, json\n"
        "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
        "print('ok')",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)

    registered = loader.scan_and_register()

    assert registered == ["good_skill"]


@pytest.mark.asyncio
async def test_nonzero_exit_code_raises_tool_execution_error(tmp_path):
    _write_skill(
        tmp_path,
        "failing",
        skill_md="---\nname: failing_skill\ndescription: always fails\n---\nbody\n",
        run_py="import sys\nsys.stderr.write('boom')\nsys.exit(1)",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)
    loader.scan_and_register()

    with pytest.raises(ToolExecutionError) as exc_info:
        await registry.dispatch("failing_skill", {})
    assert "boom" in str(exc_info.value) or "failing_skill" in str(exc_info.value)


@pytest.mark.asyncio
async def test_timeout_kills_process_and_raises(tmp_path):
    _write_skill(
        tmp_path,
        "slow",
        skill_md="---\nname: slow_skill\ndescription: sleeps forever\n---\nbody\n",
        run_py="import time\ntime.sleep(10)\nprint('should not get here')",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry, timeout_seconds=0.5)
    loader.scan_and_register()

    with pytest.raises(ToolExecutionError) as exc_info:
        await registry.dispatch("slow_skill", {})
    assert "timed out" in str(exc_info.value).lower()
