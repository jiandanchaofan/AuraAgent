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


def test_all_shipped_market_insight_skills_parse_and_register():
    """Structural-only check for the three market-insight skills (they hit
    real Google News RSS when actually called, so no network dispatch
    here — just confirming SkillLoader can discover and register them,
    each with a distinct name and a `segment` input field)."""
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry)

    registered = loader.scan_and_register()

    for name in ("market_new_products", "market_tech_trends", "market_company_moves"):
        assert name in registered, f"{name} was not discovered/registered"

    specs_by_name = {s.name: s for s in registry.get_tool_specs()}
    for name in ("market_new_products", "market_tech_trends", "market_company_moves"):
        assert "segment" in specs_by_name[name].input_schema["properties"]


@pytest.mark.asyncio
async def test_make_pptx_skill_generates_a_real_pptx_file(tmp_path):
    """Unlike the market-insight skills, make_pptx has no network
    dependency (pure local file generation via python-pptx), so this runs
    for real rather than being structural-only."""
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry)
    loader.scan_and_register()

    output_path = tmp_path / "demo.pptx"
    result = await registry.dispatch(
        "make_pptx",
        {
            "output_path": str(output_path),
            "title": "Test Deck",
            "slides": [{"title": "Slide One", "bullets": ["Point A", "Point B"]}],
        },
    )

    assert output_path.is_file()
    assert output_path.stat().st_size > 1000  # a real .pptx zip archive, not an empty/error stub
    assert "demo.pptx" in result


@pytest.mark.asyncio
async def test_non_ascii_skill_output_is_not_corrupted(tmp_path):
    """Regression test: on Windows, a child process's stdout piped (not a
    real console) does not default to UTF-8 — it falls back to the system
    codepage (e.g. GBK), silently corrupting any non-ASCII output into
    replacement characters unless PYTHONIOENCODING is forced. This is
    real corruption of the Observation text/JSONL log, not just a
    terminal rendering artifact — reproduced with make_pptx's Chinese
    status message before being fixed."""
    _write_skill(
        tmp_path,
        "chinese_output",
        skill_md="---\nname: chinese_output\ndescription: prints Chinese text\n---\nbody\n",
        run_py="import argparse\n"
        "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
        "p.parse_args()\n"
        "print('已生成测试文件（共 3 页）')\n",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)
    loader.scan_and_register()

    result = await registry.dispatch("chinese_output", {})

    assert result == "已生成测试文件（共 3 页）"
    assert "�" not in result  # no replacement characters from a codepage mismatch


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


@pytest.mark.asyncio
async def test_register_one_registers_a_single_directory_directly(tmp_path):
    """register_one() is the public seam propose_new_skill's hot-reload
    path uses (tools/self_extend/propose_skill_tool.py) — verify it works
    standalone, not just via scan_and_register()'s loop over it."""
    _write_skill(
        tmp_path,
        "standalone",
        skill_md="---\nname: standalone_skill\ndescription: registered directly\n---\nbody\n",
        run_py="import argparse, json\n"
        "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
        "args = p.parse_args()\n"
        "print('got:', json.loads(args.args_json))",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)

    name = loader.register_one(tmp_path / "standalone")

    assert name == "standalone_skill"
    result = await registry.dispatch("standalone_skill", {"x": 1})
    assert "got:" in result


@pytest.mark.asyncio
async def test_workspace_root_is_injected_as_an_env_var_when_configured(tmp_path):
    # Real bug this guards against: make_pptx (and any future skill that
    # writes user-facing output files) needs to know where "the workspace"
    # is, since its subprocess cwd is its OWN directory, not that -- see
    # skill_loader.py's module docstring.
    workspace = tmp_path / "workspace"
    store = tmp_path / "store"
    _write_skill(
        store,
        "echo_workspace_root",
        skill_md="---\nname: echo_workspace_root\ndescription: echoes the env var\n---\nbody\n",
        run_py="import argparse, os\n"
        "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
        "p.parse_args()\n"
        "print(os.environ.get('AURA_WORKSPACE_ROOT', '(not set)'))\n",
    )
    registry = ToolRegistry()
    loader = SkillLoader(store, registry, workspace_root=workspace)
    loader.scan_and_register()

    result = await registry.dispatch("echo_workspace_root", {})

    assert result == str(workspace)


@pytest.mark.asyncio
async def test_workspace_root_env_var_is_absent_when_not_configured(tmp_path):
    # Backward compatibility: every pre-existing caller (including the
    # other tests in this file) that doesn't pass workspace_root at all
    # must see no behavior change whatsoever.
    _write_skill(
        tmp_path,
        "echo_workspace_root",
        skill_md="---\nname: echo_workspace_root\ndescription: echoes the env var\n---\nbody\n",
        run_py="import argparse, os\n"
        "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
        "p.parse_args()\n"
        "print(os.environ.get('AURA_WORKSPACE_ROOT', '(not set)'))\n",
    )
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)
    loader.scan_and_register()

    result = await registry.dispatch("echo_workspace_root", {})

    assert result == "(not set)"


@pytest.mark.asyncio
async def test_make_pptx_saves_within_the_workspace_root_not_its_own_skill_directory(tmp_path):
    # Regression test for the real bug: make_pptx used to save relative to
    # its own skills_store/make_pptx/ directory (the subprocess cwd),
    # invisible to tools/files/file_tool.py's workspace-scoped tools, and
    # crashed outright for any output_path with a subdirectory (python-pptx
    # doesn't create missing parent directories).
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry, workspace_root=workspace)
    loader.scan_and_register()

    result = await registry.dispatch(
        "make_pptx",
        {
            "output_path": "reports/deck.pptx",  # a subdirectory that does not exist yet
            "title": "Test Deck",
            "slides": [{"title": "Slide One", "bullets": ["Point A", "Point B"]}],
        },
    )

    saved_path = workspace / "reports" / "deck.pptx"
    assert saved_path.is_file()
    assert saved_path.stat().st_size > 1000  # a real .pptx zip archive, not an empty/error stub
    assert "reports/deck.pptx" in result
    # And it must NOT have landed next to run.py, the pre-fix location.
    assert not (REAL_SKILLS_STORE / "make_pptx" / "reports").exists()


@pytest.mark.asyncio
async def test_make_pptx_rejects_a_path_traversal_output_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry, workspace_root=workspace)
    loader.scan_and_register()

    with pytest.raises(ToolExecutionError) as exc_info:
        await registry.dispatch(
            "make_pptx",
            {"output_path": "../escaped.pptx", "title": "x", "slides": []},
        )
    assert "outside the workspace sandbox" in str(exc_info.value)
    assert not (tmp_path / "escaped.pptx").exists()


@pytest.mark.asyncio
async def test_make_pptx_rejects_an_absolute_output_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    loader = SkillLoader(REAL_SKILLS_STORE, registry, workspace_root=workspace)
    loader.scan_and_register()

    absolute_target = tmp_path / "elsewhere.pptx"
    with pytest.raises(ToolExecutionError) as exc_info:
        await registry.dispatch(
            "make_pptx",
            {"output_path": str(absolute_target), "title": "x", "slides": []},
        )
    assert "Absolute paths are not allowed" in str(exc_info.value)
    assert not absolute_target.exists()


def test_register_one_raises_on_bad_manifest_instead_of_silently_skipping(tmp_path):
    """Unlike scan_and_register() (which logs-and-skips a bad skill),
    register_one() must propagate the error so propose_new_skill's caller
    can roll back the half-installed directory it just wrote."""
    from skills.skill_schema import SkillManifestError

    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("no front matter\n", encoding="utf-8")

    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry)

    with pytest.raises(SkillManifestError):
        loader.register_one(skill_dir)
