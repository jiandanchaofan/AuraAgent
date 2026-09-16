"""Tests for propose_new_agent — structural pre-checks that never bother a
human, the approve/decline paths, that the new delegate_to_<name> tool is
immediately callable on approval, and that the proposal survives a
restart via config/agents.json persistence."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.agent_registry import AgentRegistry
from agents.agent_definition import AgentDefinition
from agents.scoped_tool_registry import ScopedToolRegistryView
from core.exceptions import ToolExecutionError
from core.logger import AuraLogger
from core.message_types import LLMResponse
from tests.fakes import FakeConfirmationChannel, FakeLLMProvider
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.propose_agent_tool import register_propose_agent_tool


def _write_agents_config(tmp_path: Path) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps({"agents": [{"name": "orchestrator", "role": "leader", "system_prompt": "x", "capabilities": ["*"]}]}),
        encoding="utf-8",
    )
    return path


async def _calc_handler(args):
    return str(args.get("a", 0) + args.get("b", 0))


def _setup(tmp_path, decision: bool = True):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="calculate", description="adds", input_schema={"type": "object", "properties": {}}),
        _calc_handler,
    )
    agent_registry = AgentRegistry([AgentDefinition(name="orchestrator", role="leader", system_prompt="x", capabilities=["*"])])
    leader_view = ScopedToolRegistryView(registry, ["*"])
    provider = FakeLLMProvider(
        [LLMResponse(thought_text="done", tool_calls=[], stop_reason="end_turn", raw_provider_message=[])]
    )
    logger = AuraLogger(tmp_path / "logs")
    config_path = _write_agents_config(tmp_path)
    lock = asyncio.Lock()
    confirmation = FakeConfirmationChannel(decision=decision)

    register_propose_agent_tool(
        registry, agent_registry, leader_view, provider, logger, max_turns=5,
        agents_config_path=config_path, agents_config_lock=lock, confirmation_channel=confirmation,
    )
    return registry, agent_registry, leader_view, confirmation, config_path


def _valid_args(name: str = "analyst") -> dict:
    return {
        "name": name,
        "system_prompt": "You are an analyst.",
        "capabilities": ["calculate"],
        "reason": "No existing worker synthesizes findings.",
    }


@pytest.mark.asyncio
async def test_approval_adds_worker_and_hot_registers_delegate_tool(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)

    result = await registry.dispatch("propose_new_agent", _valid_args())

    assert "analyst" in result
    assert agent_registry.get("analyst").name == "analyst"
    assert "delegate_to_analyst" in {s.name for s in leader_view.get_tool_specs()}

    delegate_result = await leader_view.dispatch("delegate_to_analyst", {"task": "say hi"})
    assert delegate_result == "done"

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    names = {a["name"] for a in saved["agents"]}
    assert names == {"orchestrator", "analyst"}


@pytest.mark.asyncio
async def test_decline_makes_no_changes(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=False)

    result = await registry.dispatch("propose_new_agent", _valid_args())

    assert "declined" in result.lower()
    assert "delegate_to_analyst" not in {s.name for s in leader_view.get_tool_specs()}
    with pytest.raises(Exception):
        agent_registry.get("analyst")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert [a["name"] for a in saved["agents"]] == ["orchestrator"]


@pytest.mark.asyncio
async def test_invalid_name_rejected_before_bothering_the_human(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_agent", _valid_args(name="not a valid name!"))

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_name_colliding_with_existing_agent_rejected_before_bothering_the_human(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_agent", _valid_args(name="orchestrator"))

    assert confirmation.requests == []


@pytest.mark.asyncio
async def test_name_colliding_with_existing_delegate_tool_rejected_before_bothering_the_human(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)
    await registry.dispatch("propose_new_agent", _valid_args(name="analyst"))

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_agent", _valid_args(name="analyst"))

    assert len(confirmation.requests) == 1  # the second attempt never asked


@pytest.mark.asyncio
async def test_role_is_always_worker_even_if_a_leader_role_were_smuggled_in(tmp_path):
    """role isn't exposed in the tool's input_schema at all, so this mostly
    guards against a future signature change accidentally reintroducing it."""
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)
    args = _valid_args()
    args["role"] = "leader"  # extra, unused key — must be ignored, not honored

    await registry.dispatch("propose_new_agent", args)

    assert agent_registry.get("analyst").role == "worker"


@pytest.mark.asyncio
async def test_proposal_shown_to_human_resolves_capability_patterns_to_real_tools(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=False)
    args = _valid_args()
    args["capabilities"] = ["calculate", "*nonexistent*"]

    await registry.dispatch("propose_new_agent", args)

    reason = confirmation.requests[0].reason
    assert "'calculate' -> calculate" in reason
    assert "'*nonexistent*' -> (matches no currently-registered tool)" in reason


@pytest.mark.asyncio
async def test_empty_capabilities_rejected_before_bothering_the_human(tmp_path):
    registry, agent_registry, leader_view, confirmation, config_path = _setup(tmp_path, decision=True)
    args = _valid_args()
    args["capabilities"] = []

    with pytest.raises(ToolExecutionError):
        await registry.dispatch("propose_new_agent", args)

    assert confirmation.requests == []
