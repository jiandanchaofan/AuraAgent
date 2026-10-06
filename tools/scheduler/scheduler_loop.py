"""run_scheduler_loop -- the background poll loop that makes ScheduledTask
entries (schedule_store.py) actually fire. Started once by
core/bootstrap.py::build_app_context() (stored as AppContext.scheduler_task)
so both main.py (CLI) and gui/server.py (GUI) get it "for free" with no
extra wiring in either -- whichever process stays running is what makes
schedules fire. Closing that process just means they wait: on the next
startup, any schedule whose next_run_at is already in the past is run
once immediately (list_due() doesn't care how far in the past, and
record_run() always recomputes the next occurrence from "now", not from
the missed time) rather than silently skipped or run repeatedly to "catch
up" a backlog.

Concurrency: AppContext.run_lock guarantees at most one orchestrator.run()
executes at a time, process-wide -- a live CLI/GUI turn and a scheduled
run can never interleave (see main.py / gui/server.py's own
`async with ctx.run_lock:` around their own orchestrator.run() calls).
A scheduled run resolves its own directory/Project the exact same way a
live turn does -- cli/service.py::sync_active_directory(project_slug=
schedule.project_slug) -- and then resolves BACK to whatever
active_project.current_slug was before this run, via that same function,
once it's done. This is still a snapshot/restore, just routed through
the shared resolver instead of duplicating its field-mutation logic
inline (an earlier version of this function did that): a GUI chat
doesn't need this, since gui/server.py's _run_orchestrator() re-resolves
fresh from that chat's own persisted SessionInfo before every turn
regardless of what a scheduled run left behind in between -- but the
CLI's "current project" has no equivalent persisted source of its own to
re-derive from, it simply IS active_project.current_slug, so leaving it
pointed at whatever a background scheduled run last resolved would be a
real, user-visible bug (see ActiveProjectState's own docstring for the
closely-related bug this project already hit once from a similar
"nothing restores it" gap). The confirmation channel swap below is a
different, orthogonal concern (making an unattended run auto-decline
instead of hanging) and follows the exact same swap-then-restore shape.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from cli.service import sync_active_directory
from tools.scheduler.auto_decline_channel import AutoDeclineConfirmationChannel
from tools.scheduler.schedule_store import ScheduledTask

if TYPE_CHECKING:
    from core.bootstrap import AppContext

_MAX_RESULT_SUMMARY_CHARS = 300


async def run_scheduler_loop(ctx: "AppContext", poll_interval_seconds: float = 60.0) -> None:
    while True:
        try:
            due = await ctx.schedule_store.list_due(datetime.now())
            for schedule in due:
                await _run_one(ctx, schedule)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad iteration must never kill the whole loop
            ctx.logger.log_error(-1, f"Scheduler loop error: {exc}", agent_name="scheduler")
        await asyncio.sleep(poll_interval_seconds)


async def _run_one(ctx: "AppContext", schedule: ScheduledTask) -> None:
    async with ctx.run_lock:
        prev_confirmation = ctx.confirmation_channel.current
        # Restored in `finally` below -- see module docstring for why
        # this one case still needs an explicit restore.
        prev_project_slug = ctx.active_project.current_slug
        try:
            ctx.confirmation_channel.set_current(AutoDeclineConfirmationChannel(ctx.logger))
            try:
                # Resolves workspace_root/active_project/the Leader's
                # dynamic tool patterns/system prompt exactly the way a
                # live turn does -- schedule.project_slug=None lands on
                # the persisted default, same as an unaffiliated live chat
                # would (cli/service.py::sync_active_directory()'s own
                # docstring has the full priority chain).
                # Passes ctx (AppContext) directly, not ctx.cli_context --
                # sync_active_directory() only ever does attribute access
                # (project_store/active_project/workspace_root/
                # default_workspace_root/leader_view/leader_engine/
                # base_leader_system_prompt/mcp_manager), never an
                # isinstance() check, and AppContext carries every one of
                # those as its own direct field too (same duck-typing
                # this project already leans on elsewhere, e.g.
                # core/react_engine.py's own module docstring).
                await sync_active_directory(ctx, project_slug=schedule.project_slug)
            except ValueError as exc:
                # The project this schedule pointed at was deleted/renamed
                # out from under it -- report clearly (visible in the
                # management panel's "last result") rather than crashing
                # the loop or silently running unscoped.
                await ctx.schedule_store.record_run(schedule.id, f"Skipped: {exc}")
                return

            try:
                result = await ctx.orchestrator.run(schedule.task, history=[])
                summary = result[:_MAX_RESULT_SUMMARY_CHARS]
            except Exception as exc:  # noqa: BLE001 - a failed scheduled run must not crash the loop
                summary = f"Failed: {exc}"
        finally:
            ctx.confirmation_channel.set_current(prev_confirmation)
            try:
                await sync_active_directory(ctx, project_slug=prev_project_slug)
            except ValueError:
                pass  # whatever was active before got deleted/renamed during this run; nothing sensible to restore to

    await ctx.schedule_store.record_run(schedule.id, summary)
    _notify(schedule, summary)
    # N14 (Auralis / remote access): the Windows-side implementation of
    # what the Auralis spec calls a "briefing push" -- broadcast to every
    # connected device via WebSocketSink's event_type special-case (see
    # core/logger.py::log_schedule_result's own docstring). A no-op for
    # the CLI (no matching TerminalSink renderer) and for a disconnected
    # phone, which instead catches up via GET /api/schedules' last_result
    # once it reconnects -- no separate "undelivered push" queue needed.
    ctx.logger.log_schedule_result(schedule.id, schedule.task, summary)


def _notify(schedule: ScheduledTask, summary: str) -> None:
    """Best-effort desktop notification -- fired by the scheduler itself
    (not left to the model to remember to call send_notification), so the
    user reliably hears about a run regardless of what the model did.
    Failure here (e.g. headless environment, no notification backend)
    must never affect the run's own already-recorded outcome."""
    try:
        from plyer import notification

        notification.notify(
            title="AuraAgent 定时任务完成",
            message=(summary or schedule.task)[:200],
            app_name="AuraAgent",
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - plyer's backends raise a variety of platform-specific errors
        pass
