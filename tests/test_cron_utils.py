"""Tests for tools/scheduler/cron_utils.py::describe_schedule() -- the
best-effort human-readable rendering used in confirmation cards and the
CLI/GUI management views.
"""
from __future__ import annotations

from tools.scheduler.cron_utils import describe_schedule


def test_describe_once():
    assert describe_schedule("once", "2026-10-05T15:00:00", None) == "2026-10-05 15:00 执行一次"


def test_describe_once_missing_run_at():
    assert describe_schedule("once", None, None) == "(未设置时间)"


def test_describe_once_unparseable_run_at_falls_back_to_raw():
    assert describe_schedule("once", "not-a-date", None) == "not-a-date 执行一次"


def test_describe_hourly():
    assert describe_schedule("recurring", None, "30 * * * *") == "每小时的第 30 分钟"


def test_describe_daily():
    assert describe_schedule("recurring", None, "0 9 * * *") == "每天 09:00"


def test_describe_weekly_single_day():
    assert describe_schedule("recurring", None, "0 10 * * 1") == "每周一 10:00"


def test_describe_weekly_multiple_days():
    assert describe_schedule("recurring", None, "0 10 * * 1,3,5") == "每周一/周三/周五 10:00"


def test_describe_weekly_sunday_is_zero():
    assert describe_schedule("recurring", None, "0 8 * * 0") == "每周日 08:00"


def test_describe_monthly():
    assert describe_schedule("recurring", None, "0 0 1 * *") == "每月 1 号 00:00"


def test_describe_yearly():
    assert describe_schedule("recurring", None, "0 0 1 1 *") == "每年 1 月 1 日 00:00"


def test_describe_recurring_missing_cron():
    assert describe_schedule("recurring", None, None) == "(未设置周期)"


def test_describe_unrecognized_pattern_falls_back_to_raw_cron():
    # day AND weekday both restricted simultaneously isn't one of the
    # shapes the GUI's own builder generates -- must fall back cleanly
    # rather than raising or guessing wrong.
    assert describe_schedule("recurring", None, "0 9 15 * 1") == "0 9 15 * 1"


def test_describe_malformed_cron_falls_back_to_raw():
    assert describe_schedule("recurring", None, "not a cron") == "not a cron"
