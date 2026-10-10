"""定时任务 Pydantic Schemas（CRUD + run-now）。

结构校验（哪个字段配哪个 schedule_type）在 schema 层；语义校验
（cron 可解析、有触发点、once_at 在未来、时区名有效）在端点层借助
services/scheduler/schedule.py 完成（错误 → ValidationError 400）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.scheduled_task import ScheduleType

ScheduleTypeLiteral = Literal["cron", "interval", "once"]

# 分钟级精度（任务明确不做秒级）；下限 60 秒
MIN_INTERVAL_SECONDS = 60


class ScheduledTaskCreate(BaseModel):
    """创建定时任务。"""

    name: str = Field(..., min_length=1, max_length=200, description="任务名")
    prompt: str = Field(..., min_length=1, max_length=20000, description="到点交给 agent 的指令")
    schedule_type: ScheduleTypeLiteral = Field(..., description="cron | interval | once")
    cron_expr: str | None = Field(
        None, max_length=100, description="5 字段 cron（分 时 日 月 周），schedule_type=cron 时必填"
    )
    interval_seconds: int | None = Field(
        None, ge=MIN_INTERVAL_SECONDS, description="固定间隔秒数，schedule_type=interval 时必填"
    )
    once_at: datetime | None = Field(
        None, description="单次触发时间（UTC；naive 输入按 UTC 理解），schedule_type=once 时必填"
    )
    timezone: str | None = Field(
        None, max_length=64, description="IANA 时区名（只影响 cron 解释；缺省用服务端默认）"
    )
    enabled: bool = Field(True, description="创建后是否立即启用")

    @model_validator(mode="after")
    def _check_fields_match_type(self) -> ScheduledTaskCreate:
        provided = {
            "cron": self.cron_expr is not None,
            "interval": self.interval_seconds is not None,
            "once": self.once_at is not None,
        }
        if not provided[self.schedule_type]:
            raise ValueError(f"schedule_type={self.schedule_type} 时必须提供对应的时间字段")
        extra = [k for k, v in provided.items() if v and k != self.schedule_type]
        if extra:
            raise ValueError(
                f"schedule_type={self.schedule_type} 不应提供其它调度字段: {', '.join(extra)}"
            )
        return self


class ScheduledTaskUpdate(BaseModel):
    """更新定时任务（不可改 schedule_type；只允许改与本任务类型相关的调度字段）。"""

    name: str | None = Field(None, min_length=1, max_length=200)
    prompt: str | None = Field(None, min_length=1, max_length=20000)
    cron_expr: str | None = Field(None, max_length=100)
    interval_seconds: int | None = Field(None, ge=MIN_INTERVAL_SECONDS)
    once_at: datetime | None = None
    timezone: str | None = Field(None, max_length=64)


class ScheduledTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prompt: str
    schedule_type: ScheduleType
    cron_expr: str | None
    interval_seconds: int | None
    once_at: datetime | None
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_task_id: str | None
    last_error: str | None
    run_count: int
    skipped_overlap_count: int
    created_by: int
    organization_id: int | None
    created_at: datetime | None
    updated_at: datetime | None


class ScheduledTaskListResponse(BaseModel):
    items: list[ScheduledTaskResponse]
    total: int


class ScheduledTaskRunNowResponse(BaseModel):
    """run-now 结果：triggered=False 时 reason 说明原因（overlap/paused 等）。"""

    triggered: bool
    reason: str
