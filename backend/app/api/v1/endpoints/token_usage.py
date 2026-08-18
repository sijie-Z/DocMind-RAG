"""API endpoints for token usage tracking and cost analytics."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
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
) -> None:
    """Record a token usage entry. Called internally by pipeline/agent."""
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
    )
    async with AsyncSessionLocal() as ts:
        ts.add(record)
        await ts.commit()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimation per 1K tokens (USD)."""
    model = model.lower()
    rates = {
        "deepseek": (0.0005, 0.002),
        "deepseek-chat": (0.0005, 0.002),
        "gpt-4": (0.03, 0.06),
        "gpt-4o": (0.01, 0.03),
        "gpt-4o-mini": (0.00015, 0.0006),
        "claude": (0.008, 0.024),
        "gemini": (0.0005, 0.0015),
    }
    in_rate, out_rate = (0.0005, 0.002)  # default: DeepSeek-like
    for key, (i_r, o_r) in rates.items():
        if key in model:
            in_rate, out_rate = i_r, o_r
            break
    return (input_tokens / 1000 * in_rate) + (output_tokens / 1000 * out_rate)


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
        db, data.user_id, data.organization_id,
        data.model, data.source,
        data.input_tokens, data.output_tokens,
    )
    return {"success": True}


@router.get("/summary", response_model=TokenUsageSummary)
async def get_token_usage_summary(
    days: int = Query(7, ge=1, le=365, description="统计天数"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get summary of token usage for dashboards."""
    if not current_user.is_superuser:
        raise AuthorizationError(detail="需要管理员权限")

    since = datetime.now(UTC) - timedelta(days=days)

    # Total stats
    total = await db.execute(
        select(
            func.coalesce(func.sum(TokenUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(TokenUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(TokenUsageRecord.cost_usd), 0),
            func.count(TokenUsageRecord.id),
        ).where(TokenUsageRecord.created_at >= since)
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
        .where(TokenUsageRecord.created_at >= since)
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
        .where(TokenUsageRecord.created_at >= since)
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
        .where(TokenUsageRecord.created_at >= since)
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

    return TokenUsageSummary(
        total_input_tokens=int(total_input or 0),
        total_output_tokens=int(total_output or 0),
        total_cost_usd=round(float(total_cost or 0), 4),
        total_requests=int(total_count or 0),
        by_model=by_model_list,
        by_user=by_user_list,
        by_day=by_day_list,
    )
