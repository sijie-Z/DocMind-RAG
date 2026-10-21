"""W2-T7（第三轮终检 §2.4/§八「配置了却没反应」批）管理仪表盘后端真值用例。

两处后端真源修正的判别：

1. **/monitoring/admin-summary 会话周环比**：新增 ``sessions_this_week`` /
   ``sessions_last_week``（近 7 日 / 前 7 日新增会话，ChatSession.created_at
   口径、org 作用域同相邻计数）。修前后端没有任何环比数据——前端「总会话
   ↑ +12%（对比上周）」是写死的假趋势（§八实机实锤）。
2. **/monitoring/llm-stats 成本费率**：按 DeepSeek 真源费率（复用
   ``token_usage._estimate_cost``，deepseek = 每 1K $0.0005/$0.002）估算。
   修前按 GPT-4o-mini 费率（$0.15/$0.60 per 1M）——与实际 DeepSeek 推理栈
   不符（前端成本卡还标注"GPT-4o-mini 费率"）。

变异点：
- 删掉 admin-summary 的周环比两键 / 改回恒 0 → 用例 1 红；
- llm-stats 成本改回 GPT-4o-mini 费率公式 → 用例 2 红（与真源不齐 + 恰等于
  旧 0.75 值双断言）。
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base, Organization, User
from app.models.chat import ChatSession
from app.models.rbac import PermissionType


def _allow_permissions(perms: set[str] | None = None):
    """桩掉权限解析（返回给定权限集，默认全部）。与 issue268 用例同款。"""
    return patch(
        "app.services.permission_service.permission_service.get_user_permissions",
        new=AsyncMock(
            return_value=perms if perms is not None else {p.value for p in PermissionType}
        ),
    )


@pytest.mark.asyncio
async def test_admin_summary_session_week_over_week(client: TestClient):
    """周环比真值：org 55 = 本周 2 / 上周 1 / 更早 3；org 66 的 1 个不得混入。

    时间锚点（相对 now）：-2d ×2（本周）、-9d ×1（上周）、-30d ×3（更早）、
    org 66 -1d ×1（他组织）。断言 total_sessions 与两个新键的精确值。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now()

    def _session(user_id: int, org_id: int, days_ago: float, title: str) -> ChatSession:
        return ChatSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            organization_id=org_id,
            title=title,
            created_at=now - timedelta(days=days_ago),
        )

    async with factory() as s:
        s.add_all(
            [
                Organization(id=55, name="Org55", owner_id=0),
                Organization(id=66, name="Org66", owner_id=0),
            ]
        )
        u55 = User(username="u55", email="u55@e.com", hashed_password="x", organization_id=55)
        u66 = User(username="u66", email="u66@e.com", hashed_password="x", organization_id=66)
        s.add_all([u55, u66])
        await s.flush()
        s.add_all(
            [
                _session(u55.id, 55, 2, "this-a"),
                _session(u55.id, 55, 2.5, "this-b"),
                _session(u55.id, 55, 9, "last"),
                _session(u55.id, 55, 30, "old-a"),
                _session(u55.id, 55, 60, "old-b"),
                _session(u55.id, 55, 90, "old-c"),
                _session(u66.id, 66, 1, "other-org"),  # 他组织：不得计入
            ]
        )
        await s.commit()

    user = SimpleNamespace(
        id=550, organization_id=55, is_superuser=False, role="admin", is_active=True
    )

    async def _ref():
        return user

    async def _db_gen():
        async with factory() as session:
            yield session

    from app.main import app

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = _ref
    try:
        with _allow_permissions():
            r = client.get("/api/v1/monitoring/admin-summary")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_sessions"] == 6, f"org 55 共计 6 个（org 66 的不得混入）: {data}"
    assert data["sessions_this_week"] == 2, f"本周（近 7 日）新增必须是真实计数: {data}"
    assert data["sessions_last_week"] == 1, f"上周（7~14 日）新增必须是真实计数: {data}"


@pytest.mark.asyncio
async def test_llm_stats_cost_uses_deepseek_rate(monkeypatch: pytest.MonkeyPatch):
    """成本 = DeepSeek 真源费率（token_usage._estimate_cost）而非 GPT-4o-mini。

    1M input + 1M output：DeepSeek 费率为 $2.5（0.5 + 2.0）；
    修前 GPT-4o-mini 公式恰为 $0.75（0.15 + 0.6）——双断言（齐真源 + 非旧值）。
    """
    from app.api.v1.endpoints import monitoring as mon
    from app.api.v1.endpoints.token_usage import _estimate_cost

    class _MetricsStub:
        async def get_stats(self):  # noqa: ANN201
            return {"response_time": 0, "p95_response_time_ms": 0}

    monkeypatch.setattr(mon, "metrics_collector", _MetricsStub())

    import app.services.rag_service as rs_mod

    monkeypatch.setattr(
        rs_mod.rag_service,
        "get_metrics",
        lambda window_seconds=0: {
            "total_input_tokens": 1_000_000,
            "total_output_tokens": 1_000_000,
            "retrieval_total": 3,
        },
    )

    import app.core.prometheus as prom

    monkeypatch.setattr(prom, "read_counter_samples", lambda name: [])

    data = await mon.get_llm_stats(db=None)

    expected = round(_estimate_cost(settings.DEEPSEEK_MODEL, 1_000_000, 1_000_000), 4)
    assert data["cost_today_usd"] == pytest.approx(expected, abs=1e-4), (
        f"成本必须与 token_usage 费率真源一致（DeepSeek），实际 {data['cost_today_usd']}"
    )
    assert data["cost_today_usd"] != pytest.approx(0.75, abs=1e-4), (
        "0.75 恰为旧 GPT-4o-mini 公式（$0.15+$0.60/1M）的结果——费率不得回退"
    )
    assert data["total_tokens_today"] == 2_000_000
    assert data["cost_warning"] is False
