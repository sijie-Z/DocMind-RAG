"""零依赖 5 字段 cron 引擎（分钟 hour day-of-month month day-of-week）。

为什么自研：本仓库运行环境（本地/CI/容器）均未声明 croniter / APScheduler 依赖，
且任务明确"不做秒级精度"——5 字段 cron 的解析与 next-fire 计算用纯函数实现，
配受控时钟测试即可全覆盖，不引入新依赖。

支持的语法（数字型，不支持 JAN/MON 名字）：
    *            任意值
    a            单值
    a-b          区间（a<=b）
    */s          全区间步进
    a-b/s        区间步进
    以上逗号组合  例：1,5-7,*/15

语义（与 Vixie cron 对齐的关键两条）：
- day-of-month 与 day-of-week 同时被限定时取**并集**（OR）：任一匹配即触发；
  只有一方被限定时以该方为准；都限 `*` 则每日。判断"被限定"按语法——
  字段文本不等于 `*` 即视为限定（`*/2` 算限定）。
- day-of-week 接受 0-7，0 与 7 都表示周日（7 归一化为 0）。

时区：cron 按 `timezone` 的**墙钟**解释，返回值是 aware UTC datetime。
DST 处理：春令时跳变中不存在的墙钟时刻（如 02:30）会被跳过（该日不触发，
下一个匹配日再触发）；秋令时重复的墙钟时刻只触发一次（fold=0，第一次出现）。
跨会话/跨平台依赖：zoneinfo 在 Windows 上需要 IANA 时区数据库，本仓由
pandas 传递依赖的 tzdata 提供（requirements.txt 中 pandas 为硬依赖）。

搜索上界：next_fire 最多向后扫 8 年（覆盖世纪闰例外，"2 月 29 日"在
2096→2104 的 8 年空档也能算到或如实报错）。超出上界抛 CronError；
API 创建校验直接转换为 422。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["CronError", "CronExpr", "load_timezone", "next_fire", "parse_cron"]

# 8 年 = 覆盖 2096→2104 世纪闰空档的最长间隔
_MAX_SEARCH_DAYS = 366 * 8 + 2

_FIELD_SPECS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),  # 7 归一化为 0（周日）
)

_ITEM_RE = re.compile(r"^(?P<base>\*|\d{1,2}|\d{1,2}-\d{1,2})(?:/(?P<step>\d{1,2}))?$")


class CronError(ValueError):
    """cron 表达式非法 / 无解（8 年上界内无触发点）/ 时区名无效。"""


@dataclass(frozen=True)
class CronExpr:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]  # 0=周日 .. 6=周六
    dom_restricted: bool
    dow_restricted: bool
    source: str

    def __str__(self) -> str:  # pragma: no cover - 仅日志/调试展示
        return self.source


def load_timezone(name: str) -> ZoneInfo:
    """按 IANA 名加载时区；失败抛 CronError（含清晰原因）。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise CronError(f"未知时区名: {name!r}（需要 IANA 名，如 Asia/Shanghai）") from exc


def _parse_field(text: str, lo: int, hi: int, field_name: str) -> set[int]:
    values: set[int] = set()
    for raw_item in text.split(","):
        item = raw_item.strip()
        if not item:
            raise CronError(f"{field_name} 字段含空项: {text!r}")
        match = _ITEM_RE.match(item)
        if not match:
            raise CronError(f"{field_name} 字段非法项 {item!r}（支持 *、a、a-b、*/s、a-b/s）")
        base = match.group("base")
        step_text = match.group("step")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise CronError(f"{field_name} 字段步进必须 >=1: {item!r}")

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
            if step_text and start == end:
                raise CronError(f"{field_name} 字段单值区间不允许步进: {item!r}")
        else:
            if step_text:
                raise CronError(f"{field_name} 字段单值不允许步进: {item!r}")
            start = end = int(base)

        if start < lo or start > hi or end < lo or end > hi:
            raise CronError(f"{field_name} 字段取值越界（{lo}-{hi}）: {item!r}")
        if start > end:
            raise CronError(f"{field_name} 字段区间起点大于终点: {item!r}")

        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> CronExpr:
    """解析 5 字段 cron 表达式；非法输入抛 CronError。"""
    if not isinstance(expr, str):
        raise CronError(f"cron 表达式必须是字符串: {expr!r}")
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"cron 表达式必须是 5 字段（分 时 日 月 周），收到 {len(fields)} 个: {expr!r}")

    minute, hour, dom, month, dow = fields
    minutes = _parse_field(minute, 0, 59, "minute")
    hours = _parse_field(hour, 0, 23, "hour")
    days_of_month = _parse_field(dom, 1, 31, "day-of-month")
    months = _parse_field(month, 1, 12, "month")
    days_of_week_raw = _parse_field(dow, 0, 7, "day-of-week")
    # 7 归一化为 0（周日），同时对 0 去重
    days_of_week = frozenset(v % 7 for v in days_of_week_raw)

    return CronExpr(
        minutes=frozenset(minutes),
        hours=frozenset(hours),
        days_of_month=frozenset(days_of_month),
        months=frozenset(months),
        days_of_week=days_of_week,
        dom_restricted=(dom.strip() != "*"),
        dow_restricted=(dow.strip() != "*"),
        source=expr.strip(),
    )


def _day_matches(expr: CronExpr, day) -> bool:  # day: datetime.date
    if day.month not in expr.months:
        return False
    dom_ok = day.day in expr.days_of_month
    dow_ok = (day.isoweekday() % 7) in expr.days_of_week  # Sun=7→0, Mon=1..Sat=6
    if expr.dom_restricted and expr.dow_restricted:
        return dom_ok or dow_ok  # Vixie 语义：双限定取并集
    if expr.dom_restricted:
        return dom_ok
    if expr.dow_restricted:
        return dow_ok
    return True


def next_fire(expr: str | CronExpr, after: datetime, timezone_name: str = "UTC") -> datetime:
    """计算 `after` 之后（严格大于）最近一次触发时刻，返回 aware UTC datetime。

    - `after` 若是 naive，按 UTC 理解；
    - cron 按 `timezone_name` 的墙钟解释；
    - 8 年搜索上界内无解（如 "0 0 31 2 *"）抛 CronError。
    """
    cron = expr if isinstance(expr, CronExpr) else parse_cron(expr)
    zone = load_timezone(timezone_name)

    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    local_after = after.astimezone(zone)
    # 严格大于：先取下分钟后整（after 恰好落在分钟点上时 +1 保证不被重复命中）
    start = local_after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    day = start.date()
    sorted_hours = sorted(cron.hours)
    sorted_minutes = sorted(cron.minutes)
    for _ in range(_MAX_SEARCH_DAYS):
        if _day_matches(cron, day):
            for hh in sorted_hours:
                for mm in sorted_minutes:
                    wall = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone)
                    if wall < start:
                        continue
                    # DST：跳变中不存在的墙钟时刻（往返换算出不同墙钟）→ 该候选作废
                    round_trip = wall.astimezone(UTC).astimezone(zone)
                    if (
                        round_trip.year,
                        round_trip.month,
                        round_trip.day,
                        round_trip.hour,
                        round_trip.minute,
                    ) != (wall.year, wall.month, wall.day, wall.hour, wall.minute):
                        continue
                    return wall.astimezone(UTC)
        day += timedelta(days=1)

    raise CronError(
        f"cron 表达式在 8 年搜索范围内没有触发点（如 2 月 31 日这类无解日程）: {cron.source!r}"
    )
