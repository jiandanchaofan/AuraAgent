"""describe_schedule() -- best-effort human-readable rendering of a
scheduled task's trigger, for confirmation cards and the GUI's/CLI's
management list. Deliberately NOT a general cron descriptor (that's a
whole separate small library, cron-descriptor, not worth a second new
dependency just for display text) -- covers the handful of shapes the
GUI's own recurrence builder actually generates (single time-of-day,
optionally restricted to specific weekdays/day-of-month/month), falling
back to the raw cron expression for anything else (e.g. a human typing an
unusual expression directly via `/schedule add cron`).
"""
from __future__ import annotations

from datetime import datetime

#: cron's day-of-week: 0 and 7 both mean Sunday, 1..6 are Mon..Sat.
_WEEKDAY_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def describe_schedule(trigger_type: str, run_at: str | None, cron_expression: str | None) -> str:
    if trigger_type == "once":
        if not run_at:
            return "(未设置时间)"
        try:
            dt = datetime.fromisoformat(run_at)
        except ValueError:
            return f"{run_at} 执行一次"
        return f"{dt.strftime('%Y-%m-%d %H:%M')} 执行一次"
    if not cron_expression:
        return "(未设置周期)"
    return _describe_cron(cron_expression)


def _describe_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, day, month, weekday = parts
    try:
        if hour == "*" and minute != "*" and day == "*" and month == "*" and weekday == "*":
            return f"每小时的第 {int(minute)} 分钟"
        if hour != "*" and minute != "*":
            time_str = f"{int(hour):02d}:{int(minute):02d}"
            if day == "*" and month == "*" and weekday == "*":
                return f"每天 {time_str}"
            if day == "*" and month == "*" and weekday != "*":
                names = [_WEEKDAY_NAMES[int(d) % 7] for d in weekday.split(",")]
                return f"每{'/'.join(names)} {time_str}"
            if day != "*" and month == "*" and weekday == "*":
                return f"每月 {int(day)} 号 {time_str}"
            if day != "*" and month != "*" and weekday == "*":
                return f"每年 {int(month)} 月 {int(day)} 日 {time_str}"
    except ValueError:
        pass
    return expr
