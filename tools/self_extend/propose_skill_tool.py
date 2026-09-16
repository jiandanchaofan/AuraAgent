"""propose_new_skill — lets the LLM (Leader-only, see config/agents.json)
propose writing a brand-new Skill when it judges existing tools/skills
can't do the job. This is the most powerful, most carefully gated tool in
AuraAgent: the LLM writes the Skill's actual run.py source code, and if a
human approves it after reviewing the FULL code (not a summary), that
code gets written to disk and executed with AuraAgent's own permissions
on every future call — there is no sandbox. See
tools/self_extend/code_review.py for the static "look here first" scan
shown alongside the code during review.

Structural problems (bad name, name collision, code that doesn't even
parse) are rejected BEFORE bothering a human — only a request that could
plausibly be approved reaches the confirmation step.
"""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from skills.skill_loader import SkillLoader
from skills.skill_schema import SkillManifestError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.self_extend.code_review import scan_for_risky_patterns

_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def register_propose_skill_tool(
    registry: ToolRegistry,
    skill_loader: SkillLoader,
    skills_dir: Path,
    confirmation_channel: ConfirmationChannel,
    grant_access: Callable[[str], None],
) -> None:
    """`grant_access(tool_name)` is called after a successful install —
    normally the caller's own ScopedToolRegistryView.add_allowed_pattern,
    so the agent that proposed the skill can immediately call it without
    needing to have predicted its name in config/agents.json ahead of time.
    """

    async def propose_new_skill(args: dict[str, Any]) -> str:
        name = args["name"]
        description = args["description"]
        input_schema = args.get("input_schema") or {"type": "object", "properties": {}}
        code = args["code"]
        reason = args.get("reason", "")

        if not _NAME_PATTERN.match(name):
            raise ToolExecutionError(f"Invalid skill name '{name}': must match {_NAME_PATTERN.pattern!r}.")

        skill_dir = skills_dir / name
        if skill_dir.exists():
            raise ToolExecutionError(f"A skill named '{name}' already exists at '{skill_dir}'.")

        existing_tool_names = {spec.name for spec in registry.get_tool_specs()}
        if name in existing_tool_names:
            raise ToolExecutionError(
                f"Tool name '{name}' is already registered (native/MCP/skill) — choose a different name."
            )

        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise ToolExecutionError(
                f"Generated code has a syntax error and was not shown for review: {exc}"
            ) from exc

        warnings = scan_for_risky_patterns(code)
        proposal = _build_proposal_text(name, description, reason, code, warnings)

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_new_skill",
                arguments={"name": name},
                reason=proposal,
                risk_level="code_execution",
            )
        )
        if not approved:
            return f"User declined to install the new skill '{name}'. No files were written."

        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_build_skill_md(name, description, input_schema), encoding="utf-8")
        (skill_dir / "run.py").write_text(code, encoding="utf-8")

        try:
            registered_name = skill_loader.register_one(skill_dir)
        except (SkillManifestError, ValueError) as exc:
            shutil.rmtree(skill_dir)
            raise ToolExecutionError(f"Generated skill failed to install and was not saved: {exc}") from exc

        grant_access(registered_name)
        return (
            f"Installed and loaded new skill '{registered_name}'. It is now available as a tool "
            "for the rest of this session, and persists on disk for future sessions too."
        )

    registry.register(
        ToolSpec(
            name="propose_new_skill",
            description=(
                "Propose a brand-new Skill (a small Python script) when no existing tool or skill "
                "can accomplish something the user needs. You write the full run.py source code "
                "yourself. A human must review the ENTIRE code and approve it before it is saved "
                "and executed -- it runs with no sandbox, at the same permissions as AuraAgent "
                "itself, so keep it minimal and single-purpose. The script must parse a single "
                "--args-json '<json>' CLI argument (matching input_schema) and print its result "
                "to stdout -- see skills_store/example_skill/run.py for the exact pattern."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Identifier for the new skill (letters/digits/underscore, starts with a "
                            "letter or underscore). Becomes the tool name."
                        ),
                    },
                    "description": {"type": "string", "description": "What the skill does."},
                    "input_schema": {
                        "type": "object",
                        "description": "JSON Schema for the skill's own arguments (what --args-json will contain).",
                    },
                    "code": {
                        "type": "string",
                        "description": "Full Python source for run.py, following the --args-json convention.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why no existing tool/skill could do this instead.",
                    },
                },
                "required": ["name", "description", "code"],
            },
        ),
        propose_new_skill,
    )


def _build_skill_md(name: str, description: str, input_schema: dict[str, Any]) -> str:
    front_matter = yaml.safe_dump(
        {"name": name, "description": description, "input_schema": input_schema}, sort_keys=False
    )
    return f"---\n{front_matter}---\n\n# {name}\n\n{description}\n"


def _build_proposal_text(name: str, description: str, reason: str, code: str, warnings: list[str]) -> str:
    lines = [f"The AI wants to create a new skill: '{name}'", f"Description: {description}"]
    if reason:
        lines.append(f"Why: {reason}")
    if warnings:
        lines.append("")
        lines.append("WARNING: static scan found potentially risky patterns -- review carefully:")
        lines.extend(f"  - {w}" for w in warnings)
    lines.append("")
    lines.append(f"--- Full code (will be saved as skills_store/{name}/run.py) ---")
    lines.append(code)
    lines.append("--- End of code ---")
    lines.append("")
    lines.append(
        "This code will run on your machine with the SAME PERMISSIONS as AuraAgent itself, "
        "every time this skill is called, with NO sandbox."
    )
    return "\n".join(lines)
