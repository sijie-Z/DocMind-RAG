"""定时任务 CRUD + 手动触发 API（harness 维度 7：后台/主动任务）。

权限模型（对齐 workflow 域惯例 + org 域要求）：
- 创建：`require_organization` fail-closed 解析组织上下文（无组织 403）；
- 其余操作：任务创建者私有；**超管对本表全权——读（list/get）与写（patch/
  pause/resume/delete/run-now）**，对齐仓内 RBAC"上帝模式"惯例
  （app/core/security.py 的 permission_required 对超管整体放行），属记录在案
  的设计选择（评审 MINOR-3）；非超管访问他人任务一律 404（不泄露存在性）；
- run-now 是显式用户动作，不受 ENABLE_SCHEDULER 门控影响（门控只管自动循环）。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_organization
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.models.user import User
from app.schemas.scheduled_task import (
    ScheduledTaskCreate,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskRunNowResponse,
    ScheduledTaskUpdate,
)
from app.services.scheduler.cron import CronError, load_timezone
from app.services.scheduler.engine import scheduler_engine
from app.services.scheduler.schedule import (
    ScheduleSpecError,
    as_naive_utc,
    first_next_run,
    next_on_resume,
    utcnow_naive,
)

router = APIRouter()


async def _get_owned_task(
    db: AsyncSession, task_id: str, current_user: User
) -> ScheduledTask:
    """取任务并做归属校验：非创建者（且非超管）一律 404。

    超管是**全权**（读+写）——与仓内 permission_required 的上帝模式口径一致，
    属评审 MINOR-3 记录在案的设计选择。
    """
    task = await db.get(ScheduledTask, task_id)
    if task is None:
        raise NotFoundError(detail="定时任务不存在")
    if not current_user.is_superuser and task.created_by != current_user.id:
        raise NotFoundError(detail="定时任务不存在")
    return task


def _resolve_timezone(raw: str | None) -> str:
    name = (raw or settings.SCHEDULER_DEFAULT_TIMEZONE or "UTC").strip()
    try:
        load_timezone(name)
    except CronError as exc:
        raise ValidationError(detail=str(exc)) from None
    return name


@router.post("/", response_model=ScheduledTaskResponse)
async def create_scheduled_task(
    payload: ScheduledTaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建定时任务。语义校验失败（cron 无解 / once_at 已过期等）→ 400。"""
    org_id = await require_organization(current_user, db)
    timezone_name = _resolve_timezone(payload.timezone)
    schedule_type = ScheduleType(payload.schedule_type)

    try:
        next_run = first_next_run(
            schedule_type=schedule_type,
            now=utcnow_naive(),
            cron_expr=payload.cron_expr,
            interval_seconds=payload.interval_seconds,
            once_at=payload.once_at,
            timezone_name=timezone_name,
        )
    except ScheduleSpecError as exc:
        raise ValidationError(detail=str(exc)) from None

    task = ScheduledTask(
        name=payload.name,
        prompt=payload.prompt,
        schedule_type=schedule_type,
        cron_expr=payload.cron_expr,
        interval_seconds=payload.interval_seconds,
        once_at=as_naive_utc(payload.once_at),
        timezone=timezone_name,
        enabled=payload.enabled,
        next_run_at=next_run,
        created_by=current_user.id,
        organization_id=org_id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.get("/", response_model=ScheduledTaskListResponse)
async def list_scheduled_tasks(
    enabled: bool | None = Query(None, description="按启用状态过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出定时任务：本人创建的；超管可见全部。"""
    base = select(ScheduledTask)
    if not current_user.is_superuser:
        base = base.where(ScheduledTask.created_by == current_user.id)
    if enabled is not None:
        base = base.where(ScheduledTask.enabled == enabled)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(ScheduledTask.created_at.desc(), ScheduledTask.id)
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"items": rows, "total": total}


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_owned_task(db, task_id, current_user)


@router.patch("/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    task_id: str,
    payload: ScheduledTaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新任务。调度字段（cron/interval/once/timezone）改动后重算 next_run；
    不可改 schedule_type（删除重建即可）。已完成的单次任务可改 once_at 重新武装，
    再 resume 后生效。"""
    task = await _get_owned_task(db, task_id, current_user)
    spec_changed = False

    if payload.name is not None:
        task.name = payload.name
    if payload.prompt is not None:
        task.prompt = payload.prompt

    if payload.cron_expr is not None:
        if task.schedule_type != ScheduleType.CRON:
            raise ValidationError(detail="该任务不是 cron 类型，不能设置 cron_expr")
        task.cron_expr = payload.cron_expr
        spec_changed = True
    if payload.interval_seconds is not None:
        if task.schedule_type != ScheduleType.INTERVAL:
            raise ValidationError(detail="该任务不是 interval 类型，不能设置 interval_seconds")
        task.interval_seconds = payload.interval_seconds
        spec_changed = True
    if payload.once_at is not None:
        if task.schedule_type != ScheduleType.ONCE:
            raise ValidationError(detail="该任务不是 once 类型，不能设置 once_at")
        task.once_at = as_naive_utc(payload.once_at)
        spec_changed = True
    if payload.timezone is not None:
        task.timezone = _resolve_timezone(payload.timezone)
        spec_changed = True

    if spec_changed:
        try:
            task.next_run_at = first_next_run(
                schedule_type=task.schedule_type,
                now=utcnow_naive(),
                cron_expr=task.cron_expr,
                interval_seconds=task.interval_seconds,
                once_at=task.once_at,
                timezone_name=task.timezone,
            )
        except ScheduleSpecError as exc:
            await db.rollback()
            raise ValidationError(detail=str(exc)) from None

    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """暂停：停止自动触发（在途运行不受影响）。"""
    task = await _get_owned_task(db, task_id, current_user)
    if task.enabled:
        task.enabled = False
        await db.commit()
        await db.refresh(task)
    return task


@router.post("/{task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复：重算 next_run（暂停期间错过的周期点不补跑；过期未跑的单次任务
    立即补跑一次；已运行完成的单次任务 409——需先更新 once_at 重新武装）。"""
    task = await _get_owned_task(db, task_id, current_user)
    if task.is_completed_once:
        raise ConflictError(
            detail="单次任务已运行完成，不能恢复；如需重跑请更新 once_at 重新武装"
        )
    task.enabled = True
    try:
        task.next_run_at = next_on_resume(task, utcnow_naive())
    except ScheduleSpecError as exc:
        await db.rollback()
        raise ValidationError(detail=str(exc)) from None
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/run-now", response_model=ScheduledTaskRunNowResponse)
async def run_now_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """立即手动触发一次运行（与到点同一执行路径）：
    - 不改变时刻表（next_run_at / enabled 不动）；
    - 上一跑未完时跳过（triggered=False, reason=overlap），不排队；
    - 暂停中的任务也允许手动触发（显式动作）。
    运行在后台继续，完成后同样发站内通知。"""
    task = await _get_owned_task(db, task_id, current_user)
    result = await scheduler_engine.run_now(task.id)
    return ScheduledTaskRunNowResponse(**result)


@router.delete("/{task_id}")
async def delete_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除任务（在途运行继续；其历史留在 agent_tasks）。"""
    task = await _get_owned_task(db, task_id, current_user)
    await db.delete(task)
    await db.commit()
    return {"success": True}
