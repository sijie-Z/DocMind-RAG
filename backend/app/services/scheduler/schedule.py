"""调度时间计算 —— 纯函数（受控时钟可测），API 与引擎共用。

时间口径（全仓对齐 checkpoint.py 的既有惯例）：库里与内存里一律 **naive UTC**
（MySQL DATETIME 不存时区；SQLite 读回也是 naive）。带时区的输入一律先换算
UTC 再去掉 tzinfo 落库；cron 解释时再换到任务自己的 timezone 墙钟。

各调度类型的推进语义（产品决策，记录在此）：
- CRON：`next_after_fire` 从**当前时刻**向后找下一个点——停机/长跑期间错过的
  中间点不补跑（避免"补跑风暴"），每次触发只消费最近一个点。
- INTERVAL：固定延迟语义（触发时刻 + interval），不是固定频率——长跑之后
  不会连续补发。
- ONCE：触发后 next_run_at=None 且 enabled=False（时刻表耗尽）；暂停期间
  错过则恢复后立即补跑一次（唯一一次机会不应被静默丢弃）。
- 重叠跳过（上一跑未完）：CRON/INTERVAL 把 next_run_at 推进到"当前时刻之后"
  （消费掉错过的点）；ONCE **保持 due**（顺延到空档补跑）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.services.scheduler.cron import CronError, next_fire

__all__ = [
    "ScheduleSpecError",
    "as_naive_utc",
    "first_next_run",
    "next_after_fire",
    "next_from_now",
    "next_on_resume",
    "utcnow_naive",
]


class ScheduleSpecError(ValueError):
    """调度规格非法（创建校验失败 / 无法计算下次时间）。"""


def utcnow_naive() -> datetime:
    """当前 UTC 时间（naive，落库口径）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def as_naive_utc(dt: datetime | None) -> datetime | None:
    """把 aware 时间换算成 UTC 去 tzinfo；naive 输入按 UTC 理解原样返回。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def first_next_run(
    *,
    schedule_type: ScheduleType,
    now: datetime,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    once_at: datetime | None = None,
    timezone_name: str = "UTC",
) -> datetime:
    """创建时的首次 next_run 计算；非法规格抛 ScheduleSpecError（API → 422）。"""
    now = as_naive_utc(now)
    try:
        if schedule_type == ScheduleType.CRON:
            return as_naive_utc(next_fire(cron_expr or "", _aware(now), timezone_name))
        if schedule_type == ScheduleType.INTERVAL:
            return now + timedelta(seconds=int(interval_seconds or 0))
        if schedule_type == ScheduleType.ONCE:
            once_at_utc = as_naive_utc(once_at)
            if once_at_utc is None:
                raise ScheduleSpecError("单次任务必须给出 once_at")
            if once_at_utc <= now:
                raise ScheduleSpecError("单次任务的 once_at 必须晚于当前时间")
            return once_at_utc
    except CronError as exc:
        raise ScheduleSpecError(str(exc)) from exc
    raise ScheduleSpecError(f"未知调度类型: {schedule_type!r}")


def next_from_now(task: ScheduledTask, now: datetime) -> datetime:
    """CRON/INTERVAL：从 `now` 起算的下一个触发时刻（错过的点不补跑）。"""
    now = as_naive_utc(now)
    if task.schedule_type == ScheduleType.CRON:
        try:
            return as_naive_utc(next_fire(task.cron_expr or "", _aware(now), task.timezone))
        except CronError as exc:  # 创建已校验；运行期再炸说明存量数据异常，如实抛错
            raise ScheduleSpecError(str(exc)) from exc
    if task.schedule_type == ScheduleType.INTERVAL:
        return now + timedelta(seconds=int(task.interval_seconds or 0))
    raise ScheduleSpecError(f"next_from_now 不适用于 {task.schedule_type!r}")


def next_after_fire(task: ScheduledTask, fired_at: datetime) -> datetime | None:
    """触发成功后推进 next_run_at；ONCE → None（时刻表耗尽）。"""
    if task.schedule_type == ScheduleType.ONCE:
        return None
    return next_from_now(task, fired_at)


def next_on_resume(task: ScheduledTask, now: datetime) -> datetime:
    """恢复（resume）时的 next_run 重算。

    - ONCE：未跑过且未过期 → 保持 once_at；已过期 → 立即补跑一次（返回 now）。
      （已跑完的任务由调用方先 409 拒绝，不会走到这里。）
    - CRON/INTERVAL：原 next_run 在未来则保持；已过期 → 重算到"当前时刻之后"，
      暂停期间错过的点不补跑。
    """
    now = as_naive_utc(now)
    if task.schedule_type == ScheduleType.ONCE:
        once_at_utc = as_naive_utc(task.once_at)
        if once_at_utc is None:
            raise ScheduleSpecError("单次任务缺少 once_at，无法恢复")
        return once_at_utc if once_at_utc > now else now
    if task.next_run_at is not None and as_naive_utc(task.next_run_at) > now:
        return as_naive_utc(task.next_run_at)
    return next_from_now(task, now)
