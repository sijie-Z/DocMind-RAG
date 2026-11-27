"""批次十四 C4：组织月度 token 配额强制执行（服务层）。

覆盖边界：NULL 不限额 / 未超限 / 恰好等于配额 / 已超限 / 超管旁路 + 审计 /
审计失败不反噬 / 读取失败 fail-open / 无组织上下文 / 跨组织隔离 / 重置时点。

变异判别力：
- 删掉 ``used >= quota`` 判定 → 超限用例红；
- 删掉超管旁路审计 → 审计用例红；
- 把 fail-open 改成 re-raise → 读取失败用例红；
- 跨组织过滤写错（如读其它组织行）→ 跨组织用例红。
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.exceptions import QuotaExceededError
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.models.token_usage import TokenUsageRecord
from app.services.audit_service import audit_service
from app.services.organization_settings_service import (
    OrganizationSettingsService,
    organization_settings_service,
)

SERVICE = organization_settings_service


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Organization(id=1, name="org-a", owner_id=1),
                Organization(id=2, name="org-b", owner_id=2),
                Organization(id=3, name="org-c", owner_id=3),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


def _user(uid: int = 1, *, superuser: bool = False):
    return MagicMock(id=uid, is_superuser=superuser)


async def _set_quota(db, organization_id: int, quota: int | None) -> None:
    db.add(OrganizationSettings(organization_id=organization_id, monthly_token_quota=quota))
    await db.commit()


async def _add_usage(db, organization_id: int, tokens: int, *, user_id: int = 1) -> None:
    db.add(
        TokenUsageRecord(
            user_id=user_id,
            organization_id=organization_id,
            model="m",
            source="rag_chat",
            input_tokens=tokens,
            output_tokens=0,
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()


# ── 配额判定边界 ────────────────────────────────────────────────────────


async def test_quota_null_is_unlimited(db):
    await _set_quota(db, 1, None)
    await _add_usage(db, 1, 10_000_000)
    await SERVICE.ensure_monthly_quota(db, 1, user=_user())


async def test_no_settings_row_is_unlimited(db):
    await _add_usage(db, 1, 10_000_000)
    await SERVICE.ensure_monthly_quota(db, 1, user=_user())


async def test_usage_below_quota_passes(db):
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 99)
    await SERVICE.ensure_monthly_quota(db, 1, user=_user())


async def test_usage_equal_to_quota_is_blocked(db):
    """边界：used == quota 即超限（>= 口径，不允许再消耗）。"""
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 100)
    with pytest.raises(QuotaExceededError) as excinfo:
        await SERVICE.ensure_monthly_quota(db, 1, user=_user())

    exc = excinfo.value
    assert exc.status_code == 429
    assert exc.error_code == "ORG_TOKEN_QUOTA_EXCEEDED"
    assert exc.detail["used"] == 100
    assert exc.detail["quota"] == 100
    reset_at = datetime.fromisoformat(exc.detail["reset_at"])
    assert reset_at.tzinfo is not None
    assert reset_at > datetime.now(UTC)


async def test_usage_over_quota_is_blocked(db):
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 250)
    with pytest.raises(QuotaExceededError) as excinfo:
        await SERVICE.ensure_monthly_quota(db, 1, user=_user())
    assert excinfo.value.detail["used"] == 250


async def test_no_org_context_is_allowed(db):
    await SERVICE.ensure_monthly_quota(db, None, user=_user())


async def test_reset_at_is_first_day_of_next_month_utc(db):
    """重置时点 = 下月 1 日 00:00 UTC（跨年也要正确）。"""
    assert (
        OrganizationSettingsService._next_month_start_utc(
            datetime(2026, 11, 28, 9, 30, tzinfo=UTC)
        ).isoformat()
        == "2026-12-01T00:00:00+00:00"
    )
    assert (
        OrganizationSettingsService._next_month_start_utc(
            datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
        ).isoformat()
        == "2027-01-01T00:00:00+00:00"
    )


# ── 超管旁路 + 审计 ─────────────────────────────────────────────────────


async def test_superuser_bypass_writes_audit(db, monkeypatch):
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 300)
    calls: list[dict] = []

    async def _capture(user_id, action, **kwargs):
        calls.append({"user_id": user_id, "action": action, **kwargs})

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    await SERVICE.ensure_monthly_quota(db, 1, user=_user(7, superuser=True))

    assert len(calls) == 1
    audit = calls[0]
    assert audit["action"] == "org_token_quota_bypass"
    assert audit["target_type"] == "organization_settings"
    assert audit["target_id"] == "1"
    assert audit["user_id"] == 7
    assert '"used": 300' in audit["detail"] and '"quota": 100' in audit["detail"]


async def test_regular_member_is_not_bypassed(db, monkeypatch):
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 300)
    calls: list[dict] = []

    async def _capture(user_id, action, **kwargs):
        calls.append({"user_id": user_id, "action": action, **kwargs})

    monkeypatch.setattr(audit_service, "log_activity", _capture)
    with pytest.raises(QuotaExceededError):
        await SERVICE.ensure_monthly_quota(db, 1, user=_user(7, superuser=False))
    assert calls == []


async def test_superuser_audit_failure_does_not_break_request(db, monkeypatch, caplog):
    """审计是旁路：写审计失败也不得把超管请求打成 500，但要留 warning。"""
    await _set_quota(db, 1, 100)
    await _add_usage(db, 1, 300)

    async def _boom(user_id, action, **kwargs):
        raise RuntimeError("audit db down")

    monkeypatch.setattr(audit_service, "log_activity", _boom)
    with caplog.at_level(logging.WARNING):
        await SERVICE.ensure_monthly_quota(db, 1, user=_user(7, superuser=True))
    assert any("审计写入失败" in record.message for record in caplog.records)


# ── 读取失败 fail-open ──────────────────────────────────────────────────


async def test_read_failure_fails_open_with_warning(caplog):
    broken = MagicMock()
    broken.execute = MagicMock(side_effect=RuntimeError("settings db down"))
    with caplog.at_level(logging.WARNING):
        await SERVICE.ensure_monthly_quota(broken, 1, user=_user())
    assert any("fail-open" in record.message for record in caplog.records), (
        "读取失败必须 fail-open 且留可观测 warning"
    )


async def test_non_int_quota_value_is_treated_as_unset(monkeypatch, caplog):
    """值级闸门：非 int 配额（替身/驱动漂移）不得经 int() 变成 1 而误判超限。"""

    async def _fake_get_row(db, organization_id):
        return SimpleNamespace(monthly_token_quota="100")

    monkeypatch.setattr(SERVICE, "get_row", _fake_get_row)
    with caplog.at_level(logging.WARNING):
        await SERVICE.ensure_monthly_quota(MagicMock(), 1, user=_user())
    assert any("配额值类型漂移" in record.message for record in caplog.records)


async def test_non_int_usage_value_reads_as_zero(db, caplog):
    """用量 SUM 的标量类型漂移按 0 处理（不回溢成 1 触发误判）。"""
    await _set_quota(db, 1, 100)
    fake = MagicMock()
    fake.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: "oops"))
    with caplog.at_level(logging.WARNING):
        usage = await SERVICE.get_monthly_usage(fake, 1)
    assert usage == 0
    assert any("用量读取值类型漂移" in record.message for record in caplog.records)


# ── MySQL 驱动口径：SUM() 返回 Decimal ──────────────────────────────────
# 本机 MySQL 8 + aiomysql 实测：SELECT SUM(...) / COALESCE(SUM(...),0) 均返回
# Decimal，而不是 int。若只认 int，生产配额会永远按 0 用量放行（本机 SQLite
# 测试看不出来）。


async def test_get_monthly_usage_accepts_mysql_decimal():
    fake = MagicMock()
    fake.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: Decimal("150")))
    assert await SERVICE.get_monthly_usage(fake, 1) == 150


async def test_integral_decimal_usage_triggers_quota():
    """MySQL Decimal 用量必须真实参与判定并触发 429（核心回归）。"""
    row_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: SimpleNamespace(monthly_token_quota=100))
    )
    usage_result = SimpleNamespace(scalar=lambda: Decimal("150"))
    db = SimpleNamespace(execute=AsyncMock(side_effect=[row_result, usage_result]))

    with pytest.raises(QuotaExceededError) as excinfo:
        await SERVICE.ensure_monthly_quota(db, 1, user=_user())
    assert excinfo.value.detail["used"] == 150
    assert excinfo.value.detail["quota"] == 100


async def test_decimal_quota_value_is_accepted():
    row_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            first=lambda: SimpleNamespace(monthly_token_quota=Decimal("100"))
        )
    )
    usage_result = SimpleNamespace(scalar=lambda: Decimal("100"))
    db = SimpleNamespace(execute=AsyncMock(side_effect=[row_result, usage_result]))

    with pytest.raises(QuotaExceededError):
        await SERVICE.ensure_monthly_quota(db, 1, user=_user())


async def test_non_integral_decimal_usage_fails_open_with_warning(caplog):
    fake = MagicMock()
    fake.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: Decimal("1.5")))
    with caplog.at_level(logging.WARNING):
        usage = await SERVICE.get_monthly_usage(fake, 1)
    assert usage == 0
    assert any("用量读取值类型漂移" in record.message for record in caplog.records)


async def test_bool_quota_is_rejected(monkeypatch, caplog):
    """bool 是 int 子类但必须拒斥（JSON true/false 不是配额值）。"""

    async def _fake_get_row(db, organization_id):
        return SimpleNamespace(monthly_token_quota=True)

    monkeypatch.setattr(SERVICE, "get_row", _fake_get_row)
    with caplog.at_level(logging.WARNING):
        await SERVICE.ensure_monthly_quota(MagicMock(), 1, user=_user())
    assert any("配额值类型漂移" in record.message for record in caplog.records)


# ── 跨组织隔离 ──────────────────────────────────────────────────────────


async def test_cross_org_quota_isolation(db):
    """A 组织超限不得影响 B（未超限）或 C（NULL 不限额）。"""
    await _set_quota(db, 1, 100)
    await _set_quota(db, 2, 100)
    await _set_quota(db, 3, None)
    await _add_usage(db, 1, 500)
    await _add_usage(db, 2, 10, user_id=2)
    await _add_usage(db, 3, 10_000, user_id=3)

    with pytest.raises(QuotaExceededError):
        await SERVICE.ensure_monthly_quota(db, 1, user=_user(1))
    await SERVICE.ensure_monthly_quota(db, 2, user=_user(2))
    await SERVICE.ensure_monthly_quota(db, 3, user=_user(3))

    rows = (await db.execute(select(OrganizationSettings))).scalars().all()
    assert {row.organization_id for row in rows} == {1, 2, 3}
