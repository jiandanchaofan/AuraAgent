"""Tests for tools/scheduler/schedule_store.py -- ScheduledTask CRUD,
next-run-time math for both trigger types, and list_due()'s filtering.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tools.scheduler.schedule_store import ScheduleStore, compute_next_run


def _store(tmp_path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedules.json")


@pytest.mark.asyncio
async def test_create_once_schedule(tmp_path):
    store = _store(tmp_path)
    run_at = (datetime.now() + timedelta(days=1)).isoformat()

    schedule = await store.create_schedule(task="say hi", trigger_type="once", run_at=run_at)

    assert schedule.trigger_type == "once"
    assert schedule.run_at == run_at
    assert schedule.cron_expression is None
    assert schedule.next_run_at == run_at
    assert schedule.enabled is True
    assert schedule.project_slug is None


@pytest.mark.asyncio
async def test_create_once_schedule_requires_run_at(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="run_at is required"):
        await store.create_schedule(task="x", trigger_type="once")


@pytest.mark.asyncio
async def test_create_once_schedule_rejects_bad_datetime(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="Invalid ISO datetime"):
        await store.create_schedule(task="x", trigger_type="once", run_at="not-a-date")


@pytest.mark.asyncio
async def test_create_recurring_schedule_computes_next_run(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="daily check", trigger_type="recurring", cron_expression="0 9 * * *")

    assert schedule.trigger_type == "recurring"
    assert schedule.cron_expression == "0 9 * * *"
    assert schedule.run_at is None
    next_run = datetime.fromisoformat(schedule.next_run_at)
    assert next_run.hour == 9
    assert next_run.minute == 0
    assert next_run > datetime.now()


@pytest.mark.asyncio
async def test_create_recurring_schedule_requires_cron_expression(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="cron_expression is required"):
        await store.create_schedule(task="x", trigger_type="recurring")


@pytest.mark.asyncio
async def test_create_recurring_schedule_rejects_bad_cron(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="Invalid cron expression"):
        await store.create_schedule(task="x", trigger_type="recurring", cron_expression="not a cron")


@pytest.mark.asyncio
async def test_create_schedule_rejects_bad_trigger_type(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="Invalid trigger_type"):
        await store.create_schedule(task="x", trigger_type="sometimes")


@pytest.mark.asyncio
async def test_create_schedule_with_project_slug(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(
        task="x", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="ai_governance"
    )
    assert schedule.project_slug == "ai_governance"


@pytest.mark.asyncio
async def test_list_schedules_filters_by_project(tmp_path):
    store = _store(tmp_path)
    a = await store.create_schedule(task="a", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="p1")
    await store.create_schedule(task="b", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="p2")
    unaffiliated = await store.create_schedule(task="c", trigger_type="recurring", cron_expression="0 9 * * *")

    p1_only = await store.list_schedules(project_slug="p1")
    assert [s.id for s in p1_only] == [a.id]

    unaffiliated_only = await store.list_schedules(unaffiliated_only=True)
    assert [s.id for s in unaffiliated_only] == [unaffiliated.id]

    all_schedules = await store.list_schedules()
    assert len(all_schedules) == 3


@pytest.mark.asyncio
async def test_get_schedule_returns_none_for_unknown_id(tmp_path):
    store = _store(tmp_path)
    assert await store.get_schedule("nonexistent") is None


@pytest.mark.asyncio
async def test_set_enabled_toggles(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    paused = await store.set_enabled(schedule.id, False)
    assert paused.enabled is False

    resumed = await store.set_enabled(schedule.id, True)
    assert resumed.enabled is True


@pytest.mark.asyncio
async def test_set_enabled_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No such schedule"):
        await store.set_enabled("nonexistent", False)


@pytest.mark.asyncio
async def test_delete_schedule_removes_it(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    await store.delete_schedule(schedule.id)

    assert await store.get_schedule(schedule.id) is None


@pytest.mark.asyncio
async def test_delete_schedule_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No such schedule"):
        await store.delete_schedule("nonexistent")


@pytest.mark.asyncio
async def test_update_schedule_task_only(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="old", trigger_type="recurring", cron_expression="0 9 * * *")

    updated = await store.update_schedule(schedule.id, task="new")

    assert updated.task == "new"
    assert updated.cron_expression == "0 9 * * *"  # untouched


@pytest.mark.asyncio
async def test_update_schedule_switches_to_once(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
    new_run_at = (datetime.now() + timedelta(days=2)).isoformat()

    updated = await store.update_schedule(schedule.id, run_at=new_run_at)

    assert updated.trigger_type == "once"
    assert updated.run_at == new_run_at
    assert updated.cron_expression is None
    assert updated.next_run_at == new_run_at


@pytest.mark.asyncio
async def test_update_schedule_switches_to_recurring(tmp_path):
    store = _store(tmp_path)
    run_at = (datetime.now() + timedelta(days=1)).isoformat()
    schedule = await store.create_schedule(task="x", trigger_type="once", run_at=run_at)

    updated = await store.update_schedule(schedule.id, cron_expression="0 9 * * *")

    assert updated.trigger_type == "recurring"
    assert updated.cron_expression == "0 9 * * *"
    assert updated.run_at is None


@pytest.mark.asyncio
async def test_update_schedule_reactivates_a_completed_once_task(tmp_path):
    store = _store(tmp_path)
    run_at = (datetime.now() + timedelta(minutes=1)).isoformat()
    schedule = await store.create_schedule(task="x", trigger_type="once", run_at=run_at)
    await store.record_run(schedule.id, "done")
    disabled = await store.get_schedule(schedule.id)
    assert disabled.enabled is False

    new_run_at = (datetime.now() + timedelta(days=1)).isoformat()
    updated = await store.update_schedule(schedule.id, run_at=new_run_at)

    assert updated.enabled is True
    assert updated.next_run_at == new_run_at


@pytest.mark.asyncio
async def test_update_schedule_project_slug_can_be_cleared(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(
        task="x", trigger_type="recurring", cron_expression="0 9 * * *", project_slug="p1"
    )

    updated = await store.update_schedule(schedule.id, project_slug=None)

    assert updated.project_slug is None


@pytest.mark.asyncio
async def test_update_schedule_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No such schedule"):
        await store.update_schedule("nonexistent", task="x")


@pytest.mark.asyncio
async def test_record_run_recurring_recomputes_next_run_from_ran_at(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")
    old_next_run = schedule.next_run_at

    # Simulate the process having been offline for days -- record_run must
    # compute the NEXT occurrence from `ran_at` (now), not resume from the
    # originally-missed time, so an offline gap doesn't cause a burst of
    # catch-up runs.
    far_future = datetime.now() + timedelta(days=10)
    updated = await store.record_run(schedule.id, "ok", ran_at=far_future)

    assert updated.next_run_at != old_next_run
    assert datetime.fromisoformat(updated.next_run_at) > far_future
    assert updated.enabled is True
    assert updated.last_result_summary == "ok"


@pytest.mark.asyncio
async def test_record_run_once_disables_and_clears_next_run(tmp_path):
    store = _store(tmp_path)
    run_at = (datetime.now() + timedelta(minutes=1)).isoformat()
    schedule = await store.create_schedule(task="x", trigger_type="once", run_at=run_at)

    updated = await store.record_run(schedule.id, "done")

    assert updated.enabled is False
    assert updated.next_run_at is None
    assert updated.last_result_summary == "done"


@pytest.mark.asyncio
async def test_record_run_truncates_long_result_summary(tmp_path):
    store = _store(tmp_path)
    schedule = await store.create_schedule(task="x", trigger_type="recurring", cron_expression="0 9 * * *")

    updated = await store.record_run(schedule.id, "x" * 1000)

    assert len(updated.last_result_summary) == 500


@pytest.mark.asyncio
async def test_record_run_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No such schedule"):
        await store.record_run("nonexistent", "x")


@pytest.mark.asyncio
async def test_list_due_only_returns_enabled_schedules_past_next_run(tmp_path):
    store = _store(tmp_path)
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    due = await store.create_schedule(task="due", trigger_type="once", run_at=past)
    not_yet = await store.create_schedule(task="not yet", trigger_type="once", run_at=future)
    disabled_but_due = await store.create_schedule(task="disabled", trigger_type="once", run_at=past)
    await store.set_enabled(disabled_but_due.id, False)

    result = await store.list_due()

    assert [s.id for s in result] == [due.id]
    assert not_yet.id not in {s.id for s in result}
    assert disabled_but_due.id not in {s.id for s in result}


@pytest.mark.asyncio
async def test_list_due_excludes_a_completed_once_task(tmp_path):
    store = _store(tmp_path)
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    schedule = await store.create_schedule(task="x", trigger_type="once", run_at=past)
    await store.record_run(schedule.id, "done")

    assert await store.list_due() == []


def test_compute_next_run_daily():
    after = datetime(2026, 3, 1, 10, 0)
    next_run = compute_next_run("0 9 * * *", after)
    assert next_run == datetime(2026, 3, 2, 9, 0)


def test_compute_next_run_hourly():
    after = datetime(2026, 3, 1, 10, 15)
    next_run = compute_next_run("0 * * * *", after)
    assert next_run == datetime(2026, 3, 1, 11, 0)


def test_compute_next_run_weekly_multiple_days():
    # 2026-03-01 is a Sunday; "1,3,5" = Mon/Wed/Fri at 10:00
    after = datetime(2026, 3, 1, 0, 0)
    next_run = compute_next_run("0 10 * * 1,3,5", after)
    assert next_run == datetime(2026, 3, 2, 10, 0)  # the following Monday


def test_compute_next_run_month_end_edge_case():
    # "31st of every month" must skip months that don't have a 31st,
    # not crash or silently misfire -- exactly the kind of date-math
    # pitfall croniter is relied on to get right.
    after = datetime(2026, 1, 31, 12, 0)
    next_run = compute_next_run("0 0 31 * *", after)
    assert next_run == datetime(2026, 3, 31, 0, 0)  # Feb has no 31st


def test_compute_next_run_leap_year():
    after = datetime(2027, 1, 1, 0, 0)  # 2027 is not a leap year
    next_run = compute_next_run("0 0 29 2 *", after)
    assert next_run == datetime(2028, 2, 29, 0, 0)  # 2028 is


def test_compute_next_run_invalid_expression_raises_value_error():
    with pytest.raises(ValueError, match="Invalid cron expression"):
        compute_next_run("not a cron", datetime.now())
