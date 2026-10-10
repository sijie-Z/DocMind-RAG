"""scheduler 时刻表数学（schedule.py）单元测试。

覆盖：创建首跑计算 / 触发后推进（ONCE→None）/ 恢复重算（周期错过不补、
单次过期立即补跑）/ naive-UTC 归一化。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.services.scheduler.schedule import (
    ScheduleSpecError,
    as_naive_utc,
    first_next_run,
    next_after_fire,
    next_from_now,
    next_on_resume,
    utcnow_naive,
)

NOW = datetime(2026, 10, 10, 12, 0, 0)  # naive UTC


def _task(**kw) -> ScheduledTask:
    base = dict(
        id="t1",
        name="n",
        prompt="p",
        created_by=1,
        schedule_type=ScheduleType.CRON,
        cron_expr="0 9 * * *",
        timezone="UTC",
        enabled=True,
    )
    base.update(kw)
    return ScheduledTask(**base)


class TestAsNaiveUtc:
    def test_aware_converted(self):
        aware = datetime(2026, 10, 10, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        assert as_naive_utc(aware) == datetime(2026, 10, 10, 12, 0)

    def test_naive_untouched(self):
        naive = datetime(2026, 10, 10, 12, 0)
        assert as_naive_utc(naive) == naive

    def test_none_untouched(self):
        assert as_naive_utc(None) is None

    def test_utcnow_is_naive(self):
        assert utcnow_naive().tzinfo is None


class TestFirstNextRun:
    def test_cron(self):
        n = first_next_run(
            schedule_type=ScheduleType.CRON, now=NOW, cron_expr="0 9 * * *", timezone_name="UTC"
        )
        assert n == datetime(2026, 10, 11, 9, 0)

    def test_cron_shanghai(self):
        n = first_next_run(
            schedule_type=ScheduleType.CRON,
            now=NOW,
            cron_expr="0 9 * * *",
            timezone_name="Asia/Shanghai",
        )
        assert n == datetime(2026, 10, 11, 1, 0)

    def test_interval(self):
        n = first_next_run(schedule_type=ScheduleType.INTERVAL, now=NOW, interval_seconds=120)
        assert n == NOW + timedelta(seconds=120)

    def test_once_future(self):
        once = datetime(2026, 10, 10, 13, 0)
        n = first_next_run(schedule_type=ScheduleType.ONCE, now=NOW, once_at=once)
        assert n == once

    def test_once_past_rejected(self):
        with pytest.raises(ScheduleSpecError, match="必须晚于当前时间"):
            first_next_run(
                schedule_type=ScheduleType.ONCE, now=NOW, once_at=datetime(2026, 10, 10, 11, 59)
            )

    def test_once_aware_normalized(self):
        aware = datetime(2026, 10, 10, 21, 0, tzinfo=timezone(timedelta(hours=8)))
        n = first_next_run(schedule_type=ScheduleType.ONCE, now=NOW, once_at=aware)
        assert n == datetime(2026, 10, 10, 13, 0)

    def test_invalid_cron_raises_spec_error(self):
        with pytest.raises(ScheduleSpecError):
            first_next_run(
                schedule_type=ScheduleType.CRON, now=NOW, cron_expr="not a cron", timezone_name="UTC"
            )

    def test_impossible_cron_raises_spec_error(self):
        with pytest.raises(ScheduleSpecError):
            first_next_run(
                schedule_type=ScheduleType.CRON, now=NOW, cron_expr="0 0 31 2 *", timezone_name="UTC"
            )

    def test_bad_timezone_raises_spec_error(self):
        with pytest.raises(ScheduleSpecError):
            first_next_run(
                schedule_type=ScheduleType.CRON, now=NOW, cron_expr="0 9 * * *", timezone_name="X/Y"
            )


class TestNextAfterFire:
    def test_once_exhausts(self):
        assert next_after_fire(_task(schedule_type=ScheduleType.ONCE), NOW) is None

    def test_cron_from_fired_at(self):
        task = _task()
        n = next_after_fire(task, datetime(2026, 10, 10, 9, 0))
        assert n == datetime(2026, 10, 11, 9, 0)

    def test_interval_fixed_delay(self):
        task = _task(schedule_type=ScheduleType.INTERVAL, cron_expr=None, interval_seconds=300)
        assert next_after_fire(task, NOW) == NOW + timedelta(seconds=300)

    def test_missed_points_not_caught_up(self):
        """停机 3 天后恢复：从当前时刻向后算，不逐个补跑。"""
        task = _task()
        n = next_after_fire(task, datetime(2026, 10, 13, 12, 0))
        assert n == datetime(2026, 10, 14, 9, 0)


class TestNextFromNow:
    def test_once_not_applicable(self):
        with pytest.raises(ScheduleSpecError):
            next_from_now(_task(schedule_type=ScheduleType.ONCE), NOW)


class TestNextOnResume:
    def test_keeps_future_next(self):
        task = _task(next_run_at=datetime(2026, 10, 10, 18, 0))
        assert next_on_resume(task, NOW) == datetime(2026, 10, 10, 18, 0)

    def test_recomputes_past_next_and_drops_missed(self):
        task = _task(next_run_at=datetime(2026, 10, 9, 9, 0))  # 暂停期间错过
        assert next_on_resume(task, NOW) == datetime(2026, 10, 11, 9, 0)

    def test_recomputes_none_next(self):
        task = _task(next_run_at=None)
        assert next_on_resume(task, NOW) == datetime(2026, 10, 11, 9, 0)

    def test_interval_resume_from_now(self):
        task = _task(
            schedule_type=ScheduleType.INTERVAL,
            cron_expr=None,
            interval_seconds=600,
            next_run_at=datetime(2026, 10, 9, 12, 0),
        )
        assert next_on_resume(task, NOW) == NOW + timedelta(seconds=600)

    def test_once_future_kept(self):
        task = _task(schedule_type=ScheduleType.ONCE, once_at=datetime(2026, 10, 12, 0, 0))
        assert next_on_resume(task, NOW) == datetime(2026, 10, 12, 0, 0)

    def test_once_overdue_fires_immediately(self):
        """暂停期间错过的单次任务：恢复后立即补跑（返回 now → 下个 tick 触发）。"""
        task = _task(schedule_type=ScheduleType.ONCE, once_at=datetime(2026, 10, 9, 0, 0))
        assert next_on_resume(task, NOW) == NOW

    def test_once_without_once_at_raises(self):
        task = _task(schedule_type=ScheduleType.ONCE, once_at=None)
        with pytest.raises(ScheduleSpecError):
            next_on_resume(task, NOW)
