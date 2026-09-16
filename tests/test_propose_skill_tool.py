"""Tests for propose_new_skill — the highest-risk tool in AuraAgent (LLM-
written code, executed with no sandbox after human approval). Covers:
structural pre-checks that never bother the human, the approve/decline
paths, rollback on a post-approval registration failure, and that risky
code produces visible warnings in what the human is shown.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from skills.skill_loader import SkillLoader
from tests.fakes import FakeConfirmationChannel
from tools.registry import ToolRegistry
from tools.self_extend.propose_skill_tool import register_propose_skill_tool

_VALID_CODE = (
    "import argparse, json\n"
    "parser = argparse.ArgumentParser()\n"
    "parser.add_argument('--args-json', required=True)\n"
    "args = parser.parse_args()\n"
    "payload = json.loads(args.args_json)\n"
    "print(sum(payload['numbers']))\n"
)


def _setup(tmp_path, decision: bool = True):
    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    registry = ToolRegistry()
    skill_loader = SkillLoader(skills_dir, registry)
    confirmation = FakeConfirmationChannel(decision=decision)
    granted: list[str] = []
    register_propose_skill_tool(
        registry, skill_loader, skills_dir, confirmation, grant_access=granted.append
    )
    return registry, skills_dir, confirmation, granted


def _valid_args(name: str = "sum_numbers") -> dict:
    return {
        "name": name,
        "description": "Sums a list of numbers",
        "input_schema": {"type": "object", "properties": {"numbers": {"type": "array"}}, "required": ["numbers"]},
        "code": _VALID_CODE,
        "reason": "No existing tool sums numbers",
    }


@pytest.mark.asyncio
async def test_approval_installs_and_hot_registers_a_working_tool(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    result = await registry.dispatch("propose_new_skill", _valid_args())

    assert "sum_numbers" in result
    assert (skills_dir / "sum_numbers" / "SKILL.md").is_file()
    assert (skills_dir / "sum_numbers" / "run.py").read_text(encoding="utf-8") == _VALID_CODE
    assert granted == ["sum_numbers"]

    call_result = await registry.dispatch("sum_numbers", {"numbers": [1, 2, 3, 4]})
    assert call_result == "10"


@pytest.mark.asyncio
async def test_decline_writes_no_files_and_does_not_register(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=False)

    result = await registry.dispatch("propose_new_skill", _valid_args())

    assert "declined" in result.lower()
    assert not (skills_dir / "sum_numbers").exists()
    assert granted == []
    with pytest.raises(ToolExecutionError):
        await registry.dispatch("sum_numbers", {"numbers": [1]})


@pytest.mark.asyncio
async def test_invalid_name_rejected_before_bothering_the_human(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", _valid_args(name="not a valid name!"))

    assert confirmation.requests == []  # never asked


@pytest.mark.asyncio
async def test_existing_skill_directory_rejected_before_bothering_the_human(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)
    (skills_dir / "sum_numbers").mkdir()

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", _valid_args())

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_name_colliding_with_existing_tool_rejected_before_bothering_the_human(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", _valid_args(name="propose_new_skill"))

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_syntax_error_rejected_before_bothering_the_human(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)
    args = _valid_args()
    args["code"] = "def broken(:\n    pass\n"

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", args)

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_risky_code_shows_warnings_in_the_human_facing_proposal(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=False)
    args = _valid_args()
    args["code"] = "import subprocess\nsubprocess.run(['ls'])\n"

    await registry.dispatch("propose_new_skill", args)

    assert len(confirmation.requests) == 1
    reason = confirmation.requests[0].reason
    assert "subprocess" in reason
    assert "WARNING" in reason
    assert "import subprocess" in reason  # full code is shown, not a summary


@pytest.mark.asyncio
async def test_clean_code_proposal_shows_no_warnings(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=False)

    await registry.dispatch("propose_new_skill", _valid_args())

    reason = confirmation.requests[0].reason
    assert "WARNING" not in reason


@pytest.mark.asyncio
async def test_registration_failure_after_approval_rolls_back_the_directory(tmp_path):
    """If register_one() fails for any reason after files are written
    (a manifest bug, an internal name collision, ...), the just-created
    skill directory must be removed rather than left as a half-installed
    leftover. Uses a fake loader that always fails, to trigger this branch
    directly and deterministically."""
    skills_dir = tmp_path / "skills_store"
    skills_dir.mkdir()
    registry = ToolRegistry()

    class FailingLoader:
        def register_one(self, skill_dir):
            raise ValueError("simulated registration failure")

    confirmation = FakeConfirmationChannel(decision=True)
    granted: list[str] = []
    register_propose_skill_tool(registry, FailingLoader(), skills_dir, confirmation, grant_access=granted.append)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", _valid_args())

    assert not (skills_dir / "sum_numbers").exists()
    assert granted == []


@pytest.mark.asyncio
async def test_duplicate_proposal_after_success_is_rejected_at_pre_check(tmp_path):
    registry, skills_dir, confirmation, granted = _setup(tmp_path, decision=True)
    await registry.dispatch("propose_new_skill", _valid_args())

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_skill", _valid_args())

    # The rejected second attempt must not touch the already-installed skill.
    assert (skills_dir / "sum_numbers" / "run.py").read_text(encoding="utf-8") == _VALID_CODE
