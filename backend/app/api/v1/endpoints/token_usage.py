"""API endpoints for token usage tracking and cost analytics."""

import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_bound_api_key_org_id, get_current_user, is_org_admin
from app.exceptions import AuthorizationError
from app.models.token_usage import TokenUsageRecord
from app.models.user import User
from app.schemas.token_usage import TokenUsageCreate, TokenUsageSummary

router = APIRouter()


# ── Record token usage (internal) ──────────────────────────────────
async def record_token_usage(
    db: AsyncSession,
    user_id: int,
    organization_id: int | None,
    model: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
    *,
    task_id: str | None = None,
    request_id: str | None = None,
    feature: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """Record a token usage entry. Called internally by pipeline/agent.

    维度参数均为可选关键字：旧调用点不传时落库行为完全不变（新列保持 NULL）。
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return

    # Simple cost estimation
    cost_usd = _estimate_cost(model, input_tokens, output_tokens)

    # 修复：使用独立 session 提交——调用方（如 chat_service）在共享 session 上
    # 有自己的事务，此前在此处 commit 会把调用方未提交的变更一起提交，
    # 破坏事务原子性；独立 session 则互不影响。
    from app.core.database import AsyncSessionLocal

    record = TokenUsageRecord(
        user_id=user_id,
        organization_id=organization_id,
        model=model,
        source=source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        task_id=task_id,
        request_id=request_id,
        feature=feature,
        prompt_version=prompt_version,
    )
    async with AsyncSessionLocal() as ts:
        ts.add(record)
        await ts.commit()


# 兜底费率：配置加载期已 fail-fast 校验（AISettings.CostRate），这里再兜一层，
# 防止运行期 monkeypatch / 直接改 settings 对象绕过校验时 IndexError → 500。
_FALLBACK_RATE = (0.0005, 0.002)


def _safe_rate(value: object) -> tuple[float, float] | None:
    """把运行期费率归一为合法二元组；形状/数值不对返回 None（跳过/用兜底）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    rates: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            number = float(item)
        except (OverflowError, ValueError, TypeError):
            # 10**400 之类大整数不能让读侧炸掉：该条目视为非法。
            return None
        if not math.isfinite(number) or number < 0:
            return None
        rates.append(number)
    return (rates[0], rates[1])


def _safe_rates(value: object) -> dict:
    """运行期费率表容器闸门：非 dict 一律视为空表（整体回落 default-safe）。"""
    return value if isinstance(value, dict) else {}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimation per 1K tokens (USD).

    费率来自配置 ``TOKEN_COST_RATES`` / ``TOKEN_COST_DEFAULT_RATE``，
    默认值与配置化前完全一致；匹配规则同样保持「小写模型名包含 rate key，
    按配置顺序首尾命中即止」（deepseek 必须先于 deepseek-chat 之外的泛化键）。

    配置加载期（AISettings.CostRate）拒绝非法费率；本函数再做一次读侧形状
    校验：即使运行时被绕过校验改成坏值，也安全回落到默认/旧费率，绝不 IndexError。
    """
    model = model.lower()
    in_rate, out_rate = _safe_rate(settings.TOKEN_COST_DEFAULT_RATE) or _FALLBACK_RATE
    for key, rate in _safe_rates(settings.TOKEN_COST_RATES).items():
        # 超长/非法 key 直接跳过，不做 lower()/in 匹配（避免异常或退化匹配）。
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 100
            or any(ch.isspace() for ch in key)
            or key.lower() not in model
        ):
            continue
        valid = _safe_rate(rate)
        if valid is not None:
            in_rate, out_rate = valid
            break
    try:
        cost = (input_tokens / 1000 * in_rate) + (output_tokens / 1000 * out_rate)
    except (OverflowError, ValueError, TypeError):
        # 极端 token 数不会 500；用 0 成本诚实表示"无法估算"而非伪造数字。
        return 0.0
    return cost if math.isfinite(cost) else 0.0


# ── API endpoints ──────────────────────────────────────────────────


@router.post("/records", response_model=dict)
async def create_token_record(
    data: TokenUsageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually record a token usage entry (admin only)."""
    if not current_user.is_superuser:
        raise AuthorizationError(detail="需要管理员权限")
    await record_token_usage(
        db,
        data.user_id,
        data.organization_id,
        data.model,
        data.source,
        data.input_tokens,
        data.output_tokens,
    )
    return {"success": True}


@router.get("/summary", response_model=TokenUsageSummary, response_model_exclude_unset=True)
async def get_token_usage_summary(
    days: int = Query(7, ge=1, le=365, description="统计天数"),
    organization_id: int | None = Query(
        None, description="按组织过滤（超管可指定；组织管理员仅限本组织）"
    ),
    feature: str | None = Query(
        None, max_length=50, description="按功能过滤（如 rag_chat / agent）"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get summary of token usage for dashboards.

    平台超管保持全局视角（可选 organization_id/feature 过滤）；组织管理员
    只能看本组织，显式传别的组织号一律 403。组织过滤作用于全部聚合（total /
    by_model / by_user / by_day / by_organization / by_feature），杜绝通过任一
    切片泄露他组织成本。
    """
    if not current_user.is_superuser:
        # 与 organization_settings 同口径：组织绑定 API key 的组织是不可漂移的
        # 唯一上下文；普通 JWT 用户用当前组织。
        effective_org_id = get_bound_api_key_org_id(current_user) or current_user.organization_id
        if not await is_org_admin(db, current_user, effective_org_id):
            raise AuthorizationError(detail="需要组织管理员权限")
        if organization_id is not None and organization_id != effective_org_id:
            raise AuthorizationError(detail="无权查看其他组织的成本数据")
        # 非超管始终强制锁定本组织，忽略请求里的组织参数歧义。
        organization_id = effective_org_id

    # feature="" 等同未过滤：不得触发新维度键的序列化（legacy JSON 兼容）。
    feature = feature.strip() if isinstance(feature, str) else feature
    if not feature:
        feature = None
    since = datetime.now(UTC) - timedelta(days=days)
    conditions = [TokenUsageRecord.created_at >= since]
    if organization_id is not None:
        conditions.append(TokenUsageRecord.organization_id == organization_id)
    if feature is not None:
        conditions.append(TokenUsageRecord.feature == feature)

    # Total stats
    total = await db.execute(
        select(
            func.coalesce(func.sum(TokenUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(TokenUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(TokenUsageRecord.cost_usd), 0),
            func.count(TokenUsageRecord.id),
        ).where(*conditions)
    )
    total_input, total_output, total_cost, total_count = total.one()

    # By model
    by_model = await db.execute(
        select(
            TokenUsageRecord.model,
            func.sum(TokenUsageRecord.input_tokens),
            func.sum(TokenUsageRecord.output_tokens),
            func.sum(TokenUsageRecord.cost_usd),
            func.count(TokenUsageRecord.id),
        )
        .where(*conditions)
        .group_by(TokenUsageRecord.model)
        .order_by(desc(func.sum(TokenUsageRecord.cost_usd)))
    )
    by_model_list = [
        {
            "model": row.model,
            "input_tokens": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "cost_usd": round(float(row[3] or 0), 4),
            "requests": int(row[4] or 0),
        }
        for row in by_model.all()
    ]

    # By user
    by_user = await db.execute(
        select(
            TokenUsageRecord.user_id,
            func.sum(TokenUsageRecord.input_tokens),
            func.sum(TokenUsageRecord.output_tokens),
            func.sum(TokenUsageRecord.cost_usd),
            func.count(TokenUsageRecord.id),
        )
        .where(*conditions)
        .group_by(TokenUsageRecord.user_id)
        .order_by(desc(func.sum(TokenUsageRecord.cost_usd)))
        .limit(20)
    )
    by_user_list = [
        {
            "user_id": row.user_id,
            "input_tokens": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "cost_usd": round(float(row[3] or 0), 4),
            "requests": int(row[4] or 0),
        }
        for row in by_user.all()
    ]

    # By day (last 7 days)
    by_day = await db.execute(
        select(
            func.date(TokenUsageRecord.created_at).label("day"),
            func.sum(TokenUsageRecord.input_tokens),
            func.sum(TokenUsageRecord.output_tokens),
            func.sum(TokenUsageRecord.cost_usd),
            func.count(TokenUsageRecord.id),
        )
        .where(*conditions)
        .group_by(func.date(TokenUsageRecord.created_at))
        .order_by(func.date(TokenUsageRecord.created_at))
    )
    by_day_list = [
        {
            "date": str(row.day),
            "input_tokens": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "cost_usd": round(float(row[3] or 0), 4),
            "requests": int(row[4] or 0),
        }
        for row in by_day.all()
    ]

    # By organization / by feature（成本归因：回答"哪个租户/功能在烧钱"）
    def _breakdown_rows(rows, key: str) -> list[dict]:
        return [
            {
                key: row[0],
                "input_tokens": int(row[1] or 0),
                "output_tokens": int(row[2] or 0),
                "cost_usd": round(float(row[3] or 0), 4),
                "requests": int(row[4] or 0),
            }
            for row in rows
        ]

    breakdown_columns = (
        func.sum(TokenUsageRecord.input_tokens),
        func.sum(TokenUsageRecord.output_tokens),
        func.sum(TokenUsageRecord.cost_usd),
        func.count(TokenUsageRecord.id),
    )
    by_organization = await db.execute(
        select(TokenUsageRecord.organization_id, *breakdown_columns)
        .where(*conditions)
        .group_by(TokenUsageRecord.organization_id)
        .order_by(desc(func.sum(TokenUsageRecord.cost_usd)))
    )
    by_feature = await db.execute(
        select(TokenUsageRecord.feature, *breakdown_columns)
        .where(*conditions)
        .group_by(TokenUsageRecord.feature)
        .order_by(desc(func.sum(TokenUsageRecord.cost_usd)))
    )

    payload = TokenUsageSummary(
        total_input_tokens=int(total_input or 0),
        total_output_tokens=int(total_output or 0),
        total_cost_usd=round(float(total_cost or 0), 4),
        total_requests=int(total_count or 0),
        by_model=by_model_list,
        by_user=by_user_list,
        by_day=by_day_list,
    )
    # 兼容契约：没有任何**新增维度**数据时，响应 JSON 与父版本逐键一致。
    # organization_id 是旧字段，不能当作"新维度存在"的判据（否则 legacy 行
    # 永远触发新键）；只认 task_id/request_id/feature/prompt_version。
    # response_model_exclude_unset=True + 只有非空才赋值 → 新键只在有值时出现。
    has_dimensions = (
        await db.execute(
            select(func.count(TokenUsageRecord.id))
            .where(*conditions)
            .where(
                or_(
                    TokenUsageRecord.feature.is_not(None),
                    TokenUsageRecord.task_id.is_not(None),
                    TokenUsageRecord.request_id.is_not(None),
                    TokenUsageRecord.prompt_version.is_not(None),
                )
            )
        )
    ).scalar_one()
    by_org_rows = (
        _breakdown_rows(by_organization.all(), "organization_id") if has_dimensions else []
    )
    by_feature_rows = _breakdown_rows(by_feature.all(), "feature") if has_dimensions else []
    if by_org_rows:
        payload.by_organization = by_org_rows
    if by_feature_rows:
        payload.by_feature = by_feature_rows
    return payload
