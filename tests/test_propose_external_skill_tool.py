"""Tests for propose_external_skill — GitHub-tree-URL parsing, raw-file
fetch (via httpx.MockTransport, no real network), structural pre-checks
(reusing skills/skill_package.py, see test_skill_package_install.py for
those in isolation), and the approve/decline paths.
"""
from __future__ import annotations

import httpx
import pytest

from core.exceptions import ToolExecutionError
from skills.skill_loader import SkillLoader
from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.self_extend.propose_external_skill_tool import register_propose_external_skill_tool

_URL = "https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf"
_SKILL_MD = "---\nname: nano_pdf\ndescription: Edit PDFs\n---\nbody\n"
_RUN_PY = (
    "import argparse\n"
    "p = argparse.ArgumentParser(); p.add_argument('--args-json', required=True)\n"
    "p.parse_args()\n"
    "print('ok')\n"
)


def _raw_content_transport(skill_md: str | None = _SKILL_MD, run_py: str | None = _RUN_PY) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "raw.githubusercontent.com/openclaw/openclaw/main/skills/nano-pdf" in url
        if url.endswith("SKILL.md"):
            return httpx.Response(200, text=skill_md) if skill_md is not None else httpx.Response(404)
        if url.endswith("run.py"):
            return httpx.Response(200, text=run_py) if run_py is not None else httpx.Response(404)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _setup(tmp_path, decision: bool = True, skill_md=_SKILL_MD, run_py=_RUN_PY):
    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    registry = ToolRegistry()
    skill_loader = SkillLoader(skills_dir, registry)
    http_client = httpx.AsyncClient(transport=_raw_content_transport(skill_md, run_py))
    confirmation = FakeConfirmationChannel(decision=decision)
    granted: list[str] = []

    async def grant_access(tool_name: str) -> None:
        granted.append(tool_name)

    register_propose_external_skill_tool(
        registry, skills_dir, skill_loader, http_client, 5.0, confirmation, grant_access=grant_access
    )
    return registry, skills_dir, confirmation, granted


@pytest.mark.asyncio
async def test_approval_fetches_installs_and_grants(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    result = await registry.dispatch("propose_external_skill", {"url": _URL, "reason": "found via search"})

    assert "nano_pdf" in result
    assert (skills_dir / "nano_pdf" / "run.py").read_text(encoding="utf-8") == _RUN_PY
    assert granted == ["nano_pdf"]
    call_result = await registry.dispatch("nano_pdf", {})
    assert call_result == "ok"


@pytest.mark.asyncio
async def test_decline_writes_no_files(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=False)

    result = await registry.dispatch("propose_external_skill", {"url": _URL})

    assert "declined" in result.lower()
    assert not (skills_dir / "nano_pdf").exists()
    assert granted == []


@pytest.mark.asyncio
async def test_proposal_text_warns_third_party_source_and_shows_full_code(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=False)

    await registry.dispatch("propose_external_skill", {"url": _URL})

    reason = confirmation.requests[0].reason
    assert "THIRD-PARTY" in reason
    assert "SkillsMP" in reason
    assert _RUN_PY in reason
    assert confirmation.requests[0].risk_level == "code_execution"


@pytest.mark.asyncio
async def test_non_github_url_rejected_before_any_network_call(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_external_skill", {"url": "https://example.com/not-github"})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_github_url_missing_tree_segment_rejected(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_external_skill", {"url": "https://github.com/openclaw/openclaw"})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_missing_run_py_at_url_is_rejected_before_bothering_the_human(tmp_path):
    """Regression test for a real case hit during live verification: most
    publicly indexed "Agent Skills" (what SkillsMP searches) are
    instruction-only markdown with no run.py at all -- AuraAgent's Skill
    format requires one. The error must say so plainly rather than just a
    bare 404, since this is the COMMON case, not a rare edge case."""
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True, run_py=None)

    with pytest.raises(ToolExecutionError, match="instruction-only"):
        await registry.dispatch("propose_external_skill", {"url": _URL})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_name_collision_rejected_before_bothering_the_human(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)
    (skills_dir / "nano_pdf").mkdir()

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_external_skill", {"url": _URL})

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_skill_at_repo_root_with_no_subpath(tmp_path):
    """A GitHub tree URL with no trailing path segments (skill lives at
    the repo root) must still resolve to a valid raw-content URL."""
    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    registry = ToolRegistry()
    skill_loader = SkillLoader(skills_dir, registry)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "raw.githubusercontent.com/someone/some-skill-repo/main/" in url
        if url.endswith("SKILL.md"):
            return httpx.Response(200, text=_SKILL_MD)
        if url.endswith("run.py"):
            return httpx.Response(200, text=_RUN_PY)
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    confirmation = FakeConfirmationChannel(decision=True)

    async def grant_access(tool_name: str) -> None:
        pass

    register_propose_external_skill_tool(
        registry, skills_dir, skill_loader, http_client, 5.0, confirmation, grant_access=grant_access
    )

    result = await registry.dispatch(
        "propose_external_skill", {"url": "https://github.com/someone/some-skill-repo/tree/main"}
    )

    assert "nano_pdf" in result
