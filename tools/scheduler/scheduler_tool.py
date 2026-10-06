"""propose_scheduled_task -- the LLM-facing way to set up a task that runs
later without a human re-triggering it, either once at a specific time or
repeatedly on a schedule. Callable from ANY chat, Project-scoped or not
(see schedule_store.py's module docstring) -- this is deliberately not
gated behind "you must be inside a Project" the way update_project_summary
etc. are. See scheduler_loop.py for how it's actually executed.

Always confirmed (risk_level="recurring_autonomy") -- distinct from every
existing self-extension risk tier because the risk shape is different:
not "will this one action cause harm" but "should this run unattended,
repeatedly, without asking again each time." A human reviews the concrete
task text and schedule (rendered as plain language, not a raw cron
string) before it is ever saved.
"""
from __future__ import annotations

from typing import Any

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.projects.project_store import ActiveProjectState, ProjectStore
from tools.registry import ToolRegistry
from tools.scheduler.cron_utils import describe_schedule
from tools.scheduler.schedule_store import ScheduleStore


def register_propose_schedule_tool(
    registry: ToolRegistry,
    schedule_store: ScheduleStore,
    project_store: ProjectStore,
    active: ActiveProjectState,
    confirmation_channel: ConfirmationChannel,
) -> None:
    async def propose_scheduled_task(args: dict[str, Any]) -> str:
        task = args["task"]
        trigger_type = args.get("trigger_type")
        if trigger_type not in ("once", "recurring"):
            raise ToolExecutionError(f"Invalid trigger_type '{trigger_type}' (expected 'once' or 'recurring').")
        run_at = args.get("run_at")
        cron_expression = args.get("cron_expression")

        # Omitted -> default to whichever project is currently active (may
        # itself be None, i.e. unaffiliated); explicitly named -> that one,
        # even from a chat that isn't currently "inside" it.
        requested_project = args.get("project")
        if requested_project:
            project = await project_store.get_project(requested_project)
            if project is None:
                raise ToolExecutionError(f"No project named '{requested_project}'.")
            project_slug = project.slug
        else:
            project_slug = active.current_slug

        schedule_desc = describe_schedule(trigger_type, run_at, cron_expression)
        project_label = project_slug or "（不关联任何 Project）"
        reason = (
            f"设置一个无人值守的任务：{task}\n"
            f"执行时间：{schedule_desc}\n"
            f"所属 Project：{project_label}\n\n"
            "这个任务会在没有你在场的情况下执行（可能会反复执行）。执行过程中任何原本需要向你确认的操作，"
            "会被自动拒绝，而不是等待你的回应。"
        )
        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="propose_scheduled_task",
                arguments=args,
                reason=reason,
                risk_level="recurring_autonomy",
            )
        )
        if not approved:
            return "User declined to set up this scheduled task. No changes were made."

        try:
            schedule = await schedule_store.create_schedule(
                task=task,
                trigger_type=trigger_type,
                run_at=run_at,
                cron_expression=cron_expression,
                project_slug=project_slug,
            )
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return f"Scheduled (id={schedule.id}): {schedule_desc} -- {task}"

    registry.register(
        ToolSpec(
            name="propose_scheduled_task",
            description=(
                "Set up a task to run later WITHOUT the user present -- once at a specific time, or "
                "repeatedly on a schedule. Always asks the user to confirm first, since this creates a "
                "standing, unattended commitment rather than a one-off action. For trigger_type='once', "
                "translate the user's natural-language time into `run_at` (an ISO datetime, e.g. "
                "'2026-10-05T15:00:00'). For trigger_type='recurring', translate it into a standard "
                "5-field cron expression in `cron_expression` (minute hour day month weekday), e.g. "
                "'0 9 * * *' for daily at 9am, '0 * * * *' for hourly, '0 10 * * 1,3,5' for Mon/Wed/Fri "
                "at 10am, '0 0 1 * *' for monthly on the 1st, '0 0 1 1 *' for yearly on Jan 1. `task` is "
                "a full instruction, written exactly like a chat message -- it will be executed on its "
                "own with no other context from this conversation, so make it self-contained. Omit "
                "`project` to default to whichever project is currently active (or none, if none is); "
                "pass an explicit project slug/name to target a different one, even from an unrelated chat."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Full, self-contained instruction to execute when this runs.",
                    },
                    "trigger_type": {"type": "string", "enum": ["once", "recurring"]},
                    "run_at": {"type": "string", "description": "ISO datetime -- required when trigger_type is 'once'."},
                    "cron_expression": {
                        "type": "string",
                        "description": "5-field cron expression -- required when trigger_type is 'recurring'.",
                    },
                    "project": {
                        "type": "string",
                        "description": "Target project slug/name. Omit to default to the currently active project, if any.",
                    },
                },
                "required": ["task", "trigger_type"],
            },
        ),
        propose_scheduled_task,
    )
