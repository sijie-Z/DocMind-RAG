"""scheduler cron 引擎单元测试（纯函数，受控输入，零真实时间依赖）。

覆盖任务要求的边界：跨天 / 月末 / 闰年 2 月 29 / 时区（Asia/Shanghai vs UTC）/
DST 不存在与重复墙钟时刻 / dom-dow 双限定 OR 语义 / 非法表达式 / 无解日程。
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.scheduler.cron import (
    CronError,
    load_timezone,
    next_fire,
    parse_cron,
)

UTC = UTC


# ── 解析 ────────────────────────────────────────────────────────────────


class TestParseCron:
    def test_full_star(self):
        c = parse_cron("* * * * *")
        assert c.minutes == frozenset(range(60))
        assert c.hours == frozenset(range(24))
        assert len(c.days_of_month) == 31
        assert len(c.months) == 12
        assert c.days_of_week == frozenset(range(7))
        assert not c.dom_restricted and not c.dow_restricted

    def test_step_and_list_and_range(self):
        c = parse_cron("1,5-7,*/15 */6 1-5 2 *")
        assert c.minutes == frozenset({1, 5, 6, 7, 0, 15, 30, 45})
        assert c.hours == frozenset({0, 6, 12, 18})
        assert c.days_of_month == frozenset({1, 2, 3, 4, 5})
        assert c.months == frozenset({2})
        assert c.dom_restricted and not c.dow_restricted

    def test_dow_7_normalized_to_sunday(self):
        assert parse_cron("0 0 * * 7").days_of_week == frozenset({0})
        assert parse_cron("0 0 * * 0-7").days_of_week == frozenset(range(7))

    def test_star_step_counts_as_restricted(self):
        c = parse_cron("0 0 */2 * *")
        assert c.dom_restricted and not c.dow_restricted

    @pytest.mark.parametrize(
        "expr",
        [
            "* * * *",            # 4 字段
            "* * * * * *",        # 6 字段
            "60 * * * *",         # 分越界
            "* 24 * * *",         # 时越界
            "* * 0 * *",          # 日越界（下限）
            "* * * 13 *",         # 月越界
            "* * * * 8",          # 周越界
            "10-5 * * * *",       # 区间反向
            "*/0 * * * *",        # 步进为 0
            "5/2 * * * *",        # 单值带步进
            "1-1/2 * * * *",      # 单值区间带步进
            "1,,2 * * * *",       # 空项
            "a * * * *",          # 非数字
            "JAN * * * *",        # 名字不支持
        ],
    )
    def test_invalid_expressions_raise(self, expr):
        with pytest.raises(CronError):
            parse_cron(expr)

    def test_non_string_raises(self):
        with pytest.raises(CronError):
            parse_cron(None)  # type: ignore[arg-type]


# ── next_fire：严格大于 + 基础推进 ───────────────────────────────────────


class TestNextFireBasics:
    def test_strictly_after_exact_minute(self):
        """after 恰好落在触发分钟点上 → 不得重复命中该点。"""
        after = datetime(2026, 10, 10, 12, 5, 0, tzinfo=UTC)
        assert next_fire("*/5 * * * *", after) == datetime(2026, 10, 10, 12, 10, tzinfo=UTC)

    def test_strictly_after_within_minute(self):
        after = datetime(2026, 10, 10, 12, 4, 30, tzinfo=UTC)
        assert next_fire("*/5 * * * *", after) == datetime(2026, 10, 10, 12, 5, tzinfo=UTC)

    def test_naive_after_treated_as_utc(self):
        after = datetime(2026, 10, 10, 12, 4, 30)  # naive
        result = next_fire("*/5 * * * *", after)
        assert result == datetime(2026, 10, 10, 12, 5, tzinfo=UTC)
        assert result.tzinfo is not None

    def test_cross_day(self):
        after = datetime(2026, 10, 10, 23, 59, 30, tzinfo=UTC)
        assert next_fire("0 0 * * *", after) == datetime(2026, 10, 11, 0, 0, tzinfo=UTC)

    def test_hour_and_dow(self):
        # 2026-10-10 是周六；下一个周一 9:00
        after = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
        assert next_fire("0 9 * * 1", after) == datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

    def test_dow_7_is_sunday(self):
        after = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # 周二
        assert next_fire("0 0 * * 7", after) == datetime(2026, 10, 11, 0, 0, tzinfo=UTC)


# ── next_fire：月末 / 闰年 / 无解 ────────────────────────────────────────


class TestNextFireCalendarEdges:
    def test_month_end_31_skips_short_months(self):
        """每月 31 日：4 月无 31 → 跳到 5 月 31。"""
        after = datetime(2026, 4, 15, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 31 * *", after) == datetime(2026, 5, 31, 0, 0, tzinfo=UTC)

    def test_month_end_31_crosses_year(self):
        after = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 31 * *", after) == datetime(2026, 10, 31, 0, 0, tzinfo=UTC)

    def test_leap_day(self):
        after = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 29 2 *", after) == datetime(2028, 2, 29, 0, 0, tzinfo=UTC)

    def test_leap_day_just_after_leap(self):
        after = datetime(2028, 2, 29, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 29 2 *", after) == datetime(2032, 2, 29, 0, 0, tzinfo=UTC)

    def test_impossible_date_raises(self):
        with pytest.raises(CronError, match="没有触发点"):
            next_fire("0 0 31 2 *", datetime(2026, 1, 1, tzinfo=UTC))


# ── next_fire：dom/dow 双限定 OR 语义（Vixie）────────────────────────────


class TestDomDowSemantics:
    def test_or_when_both_restricted(self):
        """15 日 或 周一：2026-10-05 是周一，其后周一 10-12 早于 15 日。"""
        after = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 15 * 1", after) == datetime(2026, 10, 12, 0, 0, tzinfo=UTC)

    def test_dom_restricted_only(self):
        after = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
        assert next_fire("0 0 15 * *", after) == datetime(2026, 11, 15, 0, 0, tzinfo=UTC)

    def test_dow_restricted_only(self):
        after = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)  # 周二
        assert next_fire("0 0 * * 1", after) == datetime(2026, 10, 19, 0, 0, tzinfo=UTC)

    def test_both_star_is_every_day(self):
        after = datetime(2026, 10, 10, 23, 59, tzinfo=UTC)
        assert next_fire("0 0 * * *", after) == datetime(2026, 10, 11, 0, 0, tzinfo=UTC)


# ── 时区与 DST ──────────────────────────────────────────────────────────


class TestTimezones:
    def test_same_wall_clock_different_instants(self):
        after = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        shanghai = next_fire("0 9 * * *", after, "Asia/Shanghai")
        utc = next_fire("0 9 * * *", after, "UTC")
        assert shanghai == datetime(2026, 10, 11, 1, 0, tzinfo=UTC)  # 09:00 CST = 01:00 UTC
        assert utc == datetime(2026, 10, 11, 9, 0, tzinfo=UTC)

    def test_invalid_timezone_raises(self):
        with pytest.raises(CronError, match="未知时区名"):
            next_fire("0 9 * * *", datetime(2026, 10, 10, tzinfo=UTC), "Mars/Olympus")
        with pytest.raises(CronError):
            load_timezone("Not/AZone")

    def test_dst_spring_forward_skips_nonexistent_wall_time(self):
        """纽约 2026-03-08 02:30 不存在（02:00→03:00 跳变）→ 该日跳过，次日再触发。"""
        ny = ZoneInfo("America/New_York")
        after = datetime(2026, 3, 7, 12, 0, tzinfo=ny)  # 03-07 当地中午
        result = next_fire("30 2 * * *", after, "America/New_York")
        local = result.astimezone(ny)
        assert (local.year, local.month, local.day, local.hour, local.minute) == (
            2026,
            3,
            9,
            2,
            30,
        )

    def test_dst_fall_back_fires_once_first_occurrence(self):
        """纽约 2026-11-01 01:30 出现两次（EDT 05:30 UTC / EST 06:30 UTC）→ 只取第一次。"""
        after = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 当地 00:00 EDT
        result = next_fire("30 1 * * *", after, "America/New_York")
        assert result == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    def test_shanghai_no_dst(self):
        """中国无夏令时：连续两天都是 01:00 UTC。"""
        d1 = next_fire("0 9 * * *", datetime(2026, 7, 1, 0, 0, tzinfo=UTC), "Asia/Shanghai")
        d2 = next_fire("0 9 * * *", d1, "Asia/Shanghai")
        assert d2 - d1 == timedelta(hours=24)
