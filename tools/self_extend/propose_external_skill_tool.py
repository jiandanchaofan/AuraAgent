"""propose_external_skill — install a Skill found via find_capability's
SkillsMP search. SkillsMP results point at a GitHub tree URL (a subfolder
within a larger repository, e.g.
https://github.com/openclaw/openclaw/tree/main/skills/nano-pdf), NOT a
downloadable zip — that's why this fetches SKILL.md/run.py directly via
GitHub's raw content API instead of reusing skills/skill_package.py's
extract_skill_files() (that's for the /skills load|install CLI commands,
which DO take a zip URL/local path).

Once the two files' text is in hand, this converges onto the exact same
skills/skill_package.py stage_skill_install()/finalize_skill_install()
pair every other Skill-install path uses — the structural pre-checks
(bad manifest, name collision) and the static risk-pattern scan are not
reimplemented here.

risk_level="code_execution", the same tier propose_new_skill uses — the
code is not written by the LLM this time, but it is still unsandboxed
code that will run with AuraAgent's own permissions, so the review bar is
identical. The one thing that IS different from propose_new_skill's
proposal text is an explicit note that SkillsMP is a third-party
aggregator, not an officially reviewed source (see
tools/self_extend/capability_search.py's module docstring for why no
official Skill marketplace exists to point at instead).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from skills.skill_loader import SkillLoader
from skills.skill_package import SkillPackageError, StagedSkillInstall, finalize_skill_install, stage_skill_install
from skills.skill_schema import SkillManifestError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


@dataclass
class _ParsedGithubTreeUrl:
    owner: str
    repo: str
    branch: str
    subpath: str  # "" if the skill lives at the repo root, not a subfolder


def register_propose_external_skill_tool(
    registry: ToolRegistry,
    skills_dir: Path,
    skill_loader: SkillLoader,
    http_client: httpx.AsyncClient,
    timeout_seconds: float,
    confirmation_channel: ConfirmationChannel,
    grant_access: Callable[[str], Awaitable[None]],
) -> None:
    """`grant_access(tool_name)` is the same composed callback
    propose_new_skill/propose_mcp_server/propose_capability_grant use (see
    main.py) — widens the caller's ScopedToolRegistryView AND persists the
    grant to config/agents.json."""

    async def propose_external_skill(args: dict[str, Any]) -> str:
        url = args["url"]
        reason = args.get("reason", "")

        parsed = _parse_github_tree_url(url)
        skill_md_text = await _fetch_raw_file(http_client, _raw_url(parsed, "SKILL.md"), timeout_seconds)
        try:
            run_py_text = await _fetch_raw_file(http_client, _raw_url(parsed, "run.py"), timeout_seconds)
        except ToolExecutionError as exc:
            # A REAL, common case (verified live against SkillsMP results,
            # not a hypothetical): most publicly indexed "Agent Skills" are
            # pure-markdown instructions for an LLM to read and follow
            # (Anthropic's general Agent Skills format), NOT an executable
            # script with AuraAgent's specific run.py + --args-json CLI
            # contract (see skills/skill_schema.py). A missing run.py here
            # usually means exactly that mismatch, not a broken URL.
            raise ToolExecutionError(
                f"{exc} This usually means the Skill is instruction-only (a SKILL.md with no "
                "executable run.py) -- AuraAgent's Skill format requires a run.py, so this "
                "particular result isn't installable here even though it may work fine in other "
                "agent tools. Not every capability that shows up in search will fit this system."
            ) from exc

        try:
            staged = stage_skill_install(skill_md_text, run_py_text, skills_dir, registry)
        except (SkillManifestError, SkillPackageError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        proposal = _build_proposal_text(staged, url, reason)
        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_external_skill",
                arguments={"url": url},
                reason=proposal,
                risk_level="code_execution",
            )
        )
        if not approved:
            return f"User declined to install the skill from '{url}'. No files were written."

        try:
            registered_name = finalize_skill_install(staged, skill_loader)
        except (SkillManifestError, ValueError) as exc:
            raise ToolExecutionError(f"Installed skill failed to register and was not saved: {exc}") from exc

        await grant_access(registered_name)
        return (
            f"Installed and loaded skill '{registered_name}' from {url}. It is now available for the "
            "rest of this session, and persists on disk for future sessions too."
        )

    registry.register(
        ToolSpec(
            name="propose_external_skill",
            description=(
                "Install a Skill found via find_capability's SkillsMP search results. `url` must be the "
                "GitHub folder URL find_capability returned (https://github.com/<owner>/<repo>/tree/"
                "<branch>/<path>), pointing at a folder containing SKILL.md and run.py. A human reviews "
                "the FULL code before it is saved and executed -- it runs with no sandbox, at the same "
                "permissions as AuraAgent itself. SkillsMP is a third-party index, not an officially "
                "reviewed source, so only propose Skills you have real reason to trust. Many indexed "
                "Skills are instruction-only (no run.py) and will be rejected with a clear error before "
                "any human review -- that is expected, not a bug, treat it as 'not installable here'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub folder URL from find_capability's SkillsMP results.",
                    },
                    "reason": {"type": "string", "description": "Why an existing tool/skill can't do this instead."},
                },
                "required": ["url"],
            },
        ),
        propose_external_skill,
    )


def _parse_github_tree_url(url: str) -> _ParsedGithubTreeUrl:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ToolExecutionError(f"'{url}' is not a https://github.com/... URL.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[2] != "tree":
        raise ToolExecutionError(
            f"'{url}' doesn't look like a GitHub folder URL "
            "(expected https://github.com/<owner>/<repo>/tree/<branch>/<path>)."
        )
    owner, repo, branch = parts[0], parts[1], parts[3]
    subpath = "/".join(parts[4:])
    return _ParsedGithubTreeUrl(owner=owner, repo=repo, branch=branch, subpath=subpath)


def _raw_url(parsed: _ParsedGithubTreeUrl, filename: str) -> str:
    path = f"{parsed.subpath}/{filename}" if parsed.subpath else filename
    return f"https://raw.githubusercontent.com/{parsed.owner}/{parsed.repo}/{parsed.branch}/{path}"


async def _fetch_raw_file(http_client: httpx.AsyncClient, url: str, timeout_seconds: float) -> str:
    try:
        response = await http_client.get(url, timeout=timeout_seconds)
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(f"Timed out fetching '{url}'.") from exc
    except httpx.HTTPError as exc:
        raise ToolExecutionError(f"Failed to fetch '{url}': {exc}") from exc

    if response.status_code == 404:
        raise ToolExecutionError(f"'{url}' does not exist (404) — check the URL points at a real Skill folder.")
    if response.status_code != 200:
        raise ToolExecutionError(f"Unexpected status {response.status_code} fetching '{url}'.")
    return response.text


def _build_proposal_text(staged: StagedSkillInstall, url: str, reason: str) -> str:
    lines = [f"The AI wants to install a Skill found via external search: '{staged.manifest.name}'", f"Source: {url}"]
    if reason:
        lines.append(f"Why: {reason}")
    lines.append("")
    lines.append(
        "NOTE: this comes from a THIRD-PARTY marketplace index (SkillsMP) of public GitHub repositories "
        "-- it is NOT an officially reviewed or endorsed source. Make sure you trust this specific "
        "repository/author before approving."
    )
    if staged.warnings:
        lines.append("")
        lines.append("WARNING: static scan found potentially risky patterns -- review carefully:")
        lines.extend(f"  - {w}" for w in staged.warnings)
    lines.append("")
    lines.append(f"--- Full code (will be saved as skills_store/{staged.manifest.name}/run.py) ---")
    lines.append(staged.run_py_text)
    lines.append("--- End of code ---")
    lines.append("")
    lines.append(
        "This code will run on your machine with the SAME PERMISSIONS as AuraAgent itself, "
        "every time this skill is called, with NO sandbox."
    )
    return "\n".join(lines)
