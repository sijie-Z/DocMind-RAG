"""P0 成本按维度归因的判别性测试。

四组判据：
1. 旧调用不传维度：record_token_usage 仍接受原位置参数，新列全 NULL；
2. 新维度落库 + summary 按 org/feature 正确聚合；
3. 普通组织管理员不能看别的组织（越权 403 且无法通过无过滤请求读到全平台）；
4. 价格配置覆盖后 cost_usd 使用新价格。

迁移验证：030 的列集合/head 关系也与模型对齐（schema guard 口径）。
"""

import inspect
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.token_usage import _estimate_cost, record_token_usage
from app.bootstrap import get_alembic_heads
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base, Organization, Role, TokenUsageRecord, User
from app.models.rbac import ORG_ADMIN_ROLE_NAME, user_organization_role_association


def _assert_legacy_record_usage_signature() -> None:
    params = list(inspect.signature(record_token_usage).parameters.values())
    positional = [p.name for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert positional == [
        "db",
        "user_id",
        "organization_id",
        "model",
        "source",
        "input_tokens",
        "output_tokens",
    ], "旧 7 个位置参数必须保持不变（零回归）"
    for p in params:
        if p.name in {"task_id", "request_id", "feature", "prompt_version"}:
            assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None, (
                "新维度必须是可选关键字参数"
            )


@pytest.fixture
async def usage_db(monkeypatch):
    """内存 SQLite + 独立 token session，覆盖 record_token_usage 的独立 session。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.core import database as db_module

    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory, raising=True)

    async def _db_gen():
        async with factory() as session:
            yield session

    yield factory, _db_gen
    await engine.dispose()


def _user(user_id: int, org_id: int, superuser: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        is_superuser=superuser,
        role="user",
        is_active=True,
    )


async def _seed(factory, *, org_ids=(11, 22)):
    async with factory() as s:
        s.add_all([Organization(id=o, name=f"org-{o}", owner_id=100) for o in org_ids])
        s.add_all(
            [
                User(
                    id=100,
                    username="u100",
                    email="u100@example.com",
                    hashed_password="x",
                    organization_id=org_ids[0],
                ),
                User(
                    id=200,
                    username="u200",
                    email="u200@example.com",
                    hashed_password="x",
                    organization_id=org_ids[1],
                ),
            ]
        )
        await s.flush()
        role = Role(name=ORG_ADMIN_ROLE_NAME)
        s.add(role)
        await s.flush()
        await s.execute(
            user_organization_role_association.insert().values(
                user_id=100, organization_id=org_ids[0], role_id=role.id
            )
        )
        await s.commit()


async def _add_record(
    factory,
    *,
    user_id: int,
    org_id: int,
    feature: str | None,
    in_tokens: int,
    out_tokens: int,
    days_ago: float = 1,
    task_id: str | None = None,
    request_id: str | None = None,
    prompt_version: str | None = None,
):
    async with factory() as s:
        s.add(
            TokenUsageRecord(
                user_id=user_id,
                organization_id=org_id,
                model="deepseek-chat",
                source="agent" if feature == "agent" else "rag_chat",
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cost_usd=_estimate_cost("deepseek-chat", in_tokens, out_tokens),
                created_at=datetime.now(UTC) - timedelta(days=days_ago),
                task_id=task_id,
                request_id=request_id,
                feature=feature,
                prompt_version=prompt_version,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_legacy_record_call_keeps_dimensions_null(usage_db):
    """旧调用：签名 7 位置参数不变，新列全 NULL，cost 仍按默认费率。"""
    factory, _db_gen = usage_db
    _assert_legacy_record_usage_signature()
    await _seed(factory)

    # 旧调用形态：全位置参数，不传任何新维度。
    await record_token_usage(factory, 100, 11, "deepseek-chat", "rag_chat", 1000, 500)

    async with factory() as s:
        rows = (await s.execute(select(TokenUsageRecord))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.task_id, row.request_id, row.feature, row.prompt_version) == (
        None,
        None,
        None,
        None,
    )
    assert row.cost_usd == pytest.approx(0.0005 + 500 / 1000 * 0.002)
    assert row.source == "rag_chat" and row.user_id == 100 and row.organization_id == 11


@pytest.mark.asyncio
async def test_new_dimensions_land_and_summary_groups_by_org_and_feature(usage_db):
    """新维度落库；超管 summary 按 org/feature 分组、过滤后 totals 一致。"""
    factory, _db_gen = usage_db
    await _seed(factory)

    # 单一入口写入新维度（关键字参数，验证落库而非仅接收）。
    await record_token_usage(
        factory,
        100,
        11,
        "deepseek-chat",
        "agent",
        1000,
        0,
        task_id="task-1",
        request_id="req-1",
        feature="agent",
        prompt_version="v2",
    )
    await _add_record(
        factory, user_id=100, org_id=11, feature="rag_chat", in_tokens=2000, out_tokens=0
    )
    await _add_record(
        factory, user_id=200, org_id=22, feature="agent", in_tokens=3000, out_tokens=0
    )

    async with factory() as s:
        row = (
            await s.execute(select(TokenUsageRecord).where(TokenUsageRecord.task_id == "task-1"))
        ).scalar_one()
    assert (row.request_id, row.feature, row.prompt_version) == ("req-1", "agent", "v2")

    from app.main import app

    async def _db_gen():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = lambda: _user(999, 11, superuser=True)
    client = TestClient(app)
    try:
        resp = client.get("/api/v1/token-usage/summary?days=7")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        by_org = {r["organization_id"]: r for r in data["by_organization"]}
        by_feature = {r["feature"]: r for r in data["by_feature"]}
        assert by_org[11]["input_tokens"] == 3000
        assert by_org[22]["input_tokens"] == 3000
        assert by_feature["agent"]["input_tokens"] == 4000
        assert by_feature["rag_chat"]["input_tokens"] == 2000
        assert data["total_input_tokens"] == 6000

        filtered = client.get("/api/v1/token-usage/summary?days=7&organization_id=22&feature=agent")
        assert filtered.status_code == 200, filtered.text
        fdata = filtered.json()
        assert fdata["total_input_tokens"] == 3000
        assert {r["organization_id"] for r in fdata["by_organization"]} == {22}
        assert {r["feature"] for r in fdata["by_feature"]} == {"agent"}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_org_admin_scoped_to_own_org(usage_db):
    """组织管理员：本组织可见、指定他组织 403、无过滤请求只见本组织。"""
    factory, _db_gen = usage_db
    await _seed(factory)
    await _add_record(
        factory, user_id=100, org_id=11, feature="agent", in_tokens=1000, out_tokens=0
    )
    await _add_record(
        factory, user_id=200, org_id=22, feature="agent", in_tokens=9999, out_tokens=0
    )

    from app.main import app

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = lambda: _user(100, 11, superuser=False)
    client = TestClient(app)
    try:
        denied = client.get("/api/v1/token-usage/summary?organization_id=22")
        assert denied.status_code == 403, denied.text

        resp = client.get("/api/v1/token-usage/summary")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_input_tokens"] == 1000, "组织管理员绝不能看到全平台数据"
        assert {r["organization_id"] for r in data["by_organization"]} == {11}
        assert data["total_cost_usd"] == pytest.approx(0.0005)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_price_config_override_changes_cost(monkeypatch, usage_db):
    """价格配置覆盖后 cost_usd 用新价格（旧硬编码费率必须失效）。"""
    assert _estimate_cost("deepseek-chat", 1000, 0) == pytest.approx(0.0005)
    monkeypatch.setattr(settings, "TOKEN_COST_RATES", {"deepseek-chat": [0.01, 0.02]})
    assert _estimate_cost("deepseek-chat", 1000, 500) == pytest.approx(0.01 + 0.01)
    monkeypatch.setattr(settings, "TOKEN_COST_DEFAULT_RATE", [0.1, 0.2])
    assert _estimate_cost("my-unlisted-model", 1000, 1000) == pytest.approx(0.3)

    # 判别性落库断言：record_token_usage 必须按配置后的费率算 cost_usd。
    factory, _ = usage_db
    await record_token_usage(factory, 100, 11, "deepseek-chat", "agent", 1000, 500)
    async with factory() as s:
        row = (await s.execute(select(TokenUsageRecord))).scalar_one()
    assert row.cost_usd == pytest.approx(0.02), "落库成本必须用配置覆盖后的新价格"


@pytest.mark.asyncio
async def test_agent_usage_accumulates_and_records_task_and_request_ids(usage_db, monkeypatch):
    """Agent 主线：usage 单一入口累计 token，服务层落库带 task_id/request_id。

    变异点：删 usage_by_model 累计或删 _record_agent_cost 接线 → 本用例红。
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agent.exec_context import ExecutionContext
    from app.agent.service import AgentService
    from app.agent.usage import record_llm_usage

    ctx = ExecutionContext(query="q")
    ctx.task_id = "agent-task-1"
    ctx.request_id = "req-42"
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
        model="deepseek-chat",
        choices=[],
    )
    record_llm_usage(response, ctx)

    assert ctx.usage_by_model == {"deepseek-chat": (1000, 200)}

    factory, _db_gen = usage_db
    await _seed(factory)
    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.api.v1.endpoints.token_usage.record_token_usage", AsyncMock(side_effect=_spy)
    )
    service = AgentService()
    await service._record_agent_cost(
        ctx=ctx,
        task_id=ctx.task_id,
        request_id=ctx.request_id,
        organization_id=11,
        user_id=100,
    )

    assert len(calls) == 1
    assert calls[0]["task_id"] == "agent-task-1"
    assert calls[0]["request_id"] == "req-42"
    assert calls[0]["feature"] == "agent"
    assert calls[0]["source"] == "agent"
    assert (calls[0]["input_tokens"], calls[0]["output_tokens"]) == (1000, 200)


@pytest.mark.asyncio
async def test_chat_records_request_id_from_contextvar(usage_db, monkeypatch):
    """Chat 主线：request_id 来自 request_id contextvar（缺失时为 None）。"""
    from unittest.mock import AsyncMock

    from app.core.request_id import request_id_var

    factory, _db_gen = usage_db
    await _seed(factory)
    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.api.v1.endpoints.token_usage.record_token_usage", AsyncMock(side_effect=_spy)
    )
    token = request_id_var.set("req-chat-7")
    try:
        from app.services.chat_service import _resolve_attribution_request_id

        assert _resolve_attribution_request_id() == "req-chat-7"
    finally:
        request_id_var.reset(token)


def test_migration_030_extends_029_head():
    """迁移 head 关系与模型列一致（schema guard 判据）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__import__("app").__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("030_cost_attribution")
    assert rev is not None
    assert rev.down_revision == "029_chat_session_share_token"
    assert get_alembic_heads() == {"038_eval_case_dedup"}

    columns = {c.name for c in TokenUsageRecord.__table__.columns}
    assert {"task_id", "request_id", "feature", "prompt_version"} <= columns
    for name in ("task_id", "request_id", "feature", "prompt_version"):
        assert TokenUsageRecord.__table__.c[name].nullable is True, "旧行必须保持 NULL"


# ── CR 修复判别测试 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_rates",
    [
        {"gpt-4": [0.03]},
        {"gpt-4": [0.01, 0.02, 0.03]},
        {"gpt-4": [-0.1, 0.02]},
        {"gpt-4": [float("nan"), 0.02]},
        {"gpt-4": ["0.1", 0.02]},
    ],
)
def test_cost_rate_config_rejects_malformed_rates(bad_rates):
    """IMPORTANT-1：配置加载期拒绝非法费率（长度/负数/NaN/类型）。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="TOKEN_COST_RATES"):
        Settings(TOKEN_COST_RATES=bad_rates)


@pytest.mark.parametrize("bad_rate", [[0.0005], [0.1, -1], [float("inf"), 0.2], "0.5"])
def test_cost_rate_config_rejects_malformed_default_rate(bad_rate):
    """IMPORTANT-1：默认费率同样校验，不能让 _estimate_cost 索引崩掉。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="TOKEN_COST_DEFAULT_RATE"):
        Settings(TOKEN_COST_DEFAULT_RATE=bad_rate)


def test_estimate_cost_runtime_malformed_rate_falls_back(monkeypatch):
    """IMPORTANT-1 读侧兜底：运行期绕过校验改成坏值也不得 IndexError。"""
    monkeypatch.setattr(settings, "TOKEN_COST_RATES", {"deepseek-chat": [0.03]})
    monkeypatch.setattr(settings, "TOKEN_COST_DEFAULT_RATE", [0.0005])
    # 坏条目被跳过 → 用合法兜底费率，不抛异常。
    assert _estimate_cost("deepseek-chat", 1000, 500) == pytest.approx(0.0005 + 0.001)
    assert _estimate_cost("deepseek-chat", 1000, 500) > 0


@pytest.mark.asyncio
async def test_chat_failed_run_still_records_cost_once(monkeypatch, usage_db):
    """IMPORTANT-2：异常终止的 run 仍落成本，且只落一次。"""
    from unittest.mock import AsyncMock

    from app.agent.config import AgentConfig
    from app.agent.events import AgentEvent
    from app.agent.exec_context import ExecutionContext
    from app.agent.service import AgentService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.api.v1.endpoints.token_usage.record_token_usage", AsyncMock(side_effect=_spy)
    )
    ctx = ExecutionContext(query="q")
    ctx.usage_by_model = {"deepseek-chat": (1000, 200)}
    fake_agent = SimpleNamespace(
        last_execution_context=ctx,
        paused_for_approval=False,
        paused_for_plan_approval=False,
        paused_for_final_commit=False,
        run_cancelled=False,
        run_failed=False,
        _final_commit_persisted=False,
    )

    async def _failing_run(*args, **kwargs):
        yield AgentEvent(type="chunk", content="partial")
        raise RuntimeError("boom")

    monkeypatch.setattr("app.agent.service.PERAgentLoop", lambda **kwargs: fake_agent)
    monkeypatch.setattr(fake_agent, "run", _failing_run, raising=False)
    monkeypatch.setattr(fake_agent, "_get_tools", lambda: [], raising=False)
    monkeypatch.setattr(AgentService, "_checkpoint_error", AsyncMock())
    monkeypatch.setattr(AgentService, "_checkpoint_disconnect", AsyncMock())
    monkeypatch.setattr("app.agent.checkpoint.create_task", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentService, "_finalize_task_terminal", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentConfig, "save_to_redis", AsyncMock())

    service = AgentService()
    with pytest.raises(RuntimeError, match="boom"):
        [_ async for _ in service.chat(query="q", organization_id=11, user_id=100)]

    assert len(calls) == 1, f"失败路径必须落且仅落一次成本：{calls}"
    assert calls[0]["input_tokens"] == 1000 and calls[0]["output_tokens"] == 200


@pytest.mark.asyncio
async def test_chat_cancelled_run_still_records_cost_once(monkeypatch, usage_db):
    """IMPORTANT-2：取消的 run 仍落成本，且只落一次。"""
    import asyncio
    from unittest.mock import AsyncMock

    from app.agent.config import AgentConfig
    from app.agent.events import AgentEvent
    from app.agent.exec_context import ExecutionContext
    from app.agent.service import AgentService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.api.v1.endpoints.token_usage.record_token_usage", AsyncMock(side_effect=_spy)
    )
    ctx = ExecutionContext(query="q")
    ctx.usage_by_model = {"deepseek-chat": (1000, 200)}
    fake_agent = SimpleNamespace(
        last_execution_context=ctx,
        paused_for_approval=False,
        paused_for_plan_approval=False,
        paused_for_final_commit=False,
        run_cancelled=False,
        run_failed=False,
        _final_commit_persisted=False,
    )

    async def _cancelled_run(*args, **kwargs):
        yield AgentEvent(type="chunk", content="partial")
        raise asyncio.CancelledError()

    monkeypatch.setattr("app.agent.service.PERAgentLoop", lambda **kwargs: fake_agent)
    monkeypatch.setattr(fake_agent, "run", _cancelled_run, raising=False)
    monkeypatch.setattr(fake_agent, "_get_tools", lambda: [], raising=False)
    monkeypatch.setattr(AgentService, "_checkpoint_disconnect", AsyncMock())
    monkeypatch.setattr("app.agent.checkpoint.create_task", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentConfig, "save_to_redis", AsyncMock())

    service = AgentService()
    with pytest.raises(asyncio.CancelledError):
        [_ async for _ in service.chat(query="q", organization_id=11, user_id=100)]

    assert len(calls) == 1, f"取消路径必须落且仅落一次成本：{calls}"
    assert calls[0]["input_tokens"] == 1000 and calls[0]["output_tokens"] == 200


@pytest.mark.asyncio
async def test_chat_success_run_records_cost_once(monkeypatch, usage_db):
    """IMPORTANT-2：正常 run 不重复落成本（与旧路径一致恰好 1 条）。"""
    from unittest.mock import AsyncMock

    from app.agent.config import AgentConfig
    from app.agent.events import AgentEvent
    from app.agent.exec_context import ExecutionContext
    from app.agent.service import AgentService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.api.v1.endpoints.token_usage.record_token_usage", AsyncMock(side_effect=_spy)
    )
    ctx = ExecutionContext(query="q")
    ctx.usage_by_model = {"deepseek-chat": (1000, 200)}
    fake_agent = SimpleNamespace(
        last_execution_context=ctx,
        paused_for_approval=False,
        paused_for_plan_approval=False,
        paused_for_final_commit=False,
        run_cancelled=False,
        run_failed=False,
        _final_commit_persisted=False,
    )

    async def _ok_run(*args, **kwargs):
        yield AgentEvent(type="chunk", content="ok")

    monkeypatch.setattr("app.agent.service.PERAgentLoop", lambda **kwargs: fake_agent)
    monkeypatch.setattr(fake_agent, "run", _ok_run, raising=False)
    monkeypatch.setattr(fake_agent, "_get_tools", lambda: [], raising=False)
    monkeypatch.setattr("app.agent.checkpoint.create_task", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentService, "_finalize_task_terminal", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentConfig, "save_to_redis", AsyncMock())

    service = AgentService()
    events = [_ async for _ in service.chat(query="q", organization_id=11, user_id=100)]
    assert events
    assert len(calls) == 1, f"正常路径也只能落一次成本：{calls}"


@pytest.mark.asyncio
async def test_legacy_summary_json_has_no_new_keys(usage_db):
    """IMPORTANT-3：旧行（无新维度）summary JSON 与父版本键集逐字一致。"""
    factory, _db_gen = usage_db
    await _seed(factory)
    # 只用旧列写入：organization_id 是旧字段，feature/task_id 等保持 NULL。
    async with factory() as s:
        s.add(
            TokenUsageRecord(
                user_id=100,
                organization_id=11,
                model="deepseek-chat",
                source="rag_chat",
                input_tokens=1000,
                output_tokens=100,
                cost_usd=0.0007,
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await s.commit()

    from app.main import app

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = lambda: _user(999, 11, superuser=True)
    client = TestClient(app)
    try:
        resp = client.get("/api/v1/token-usage/summary")
        assert resp.status_code == 200, resp.text
        assert set(resp.json()) == {
            "total_input_tokens",
            "total_output_tokens",
            "total_cost_usd",
            "total_requests",
            "by_model",
            "by_user",
            "by_day",
        }, "legacy 响应不得出现 by_organization/by_feature 新键"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


# ── CR 第二轮：最小安全修复判别测试 ──────────────────────────────────


@pytest.mark.parametrize("bad_container", [None, "oops", [1, 2], 42])
def test_estimate_cost_malformed_rates_container_falls_back(monkeypatch, bad_container):
    """容器攻击：TOKEN_COST_RATES 非 dict 时整体回落 default-safe，不抛异常。"""
    monkeypatch.setattr(settings, "TOKEN_COST_RATES", bad_container)
    monkeypatch.setattr(settings, "TOKEN_COST_DEFAULT_RATE", (0.0005, 0.002))
    cost = _estimate_cost("deepseek-chat", 1000, 500)
    assert cost == pytest.approx(0.0005 + 0.001)
    assert math.isfinite(cost)


def test_estimate_cost_skips_malformed_keys_and_rates(monkeypatch):
    """超长/空白 key、非二元组、长度≠2 的 rate 全部安全跳过。"""
    monkeypatch.setattr(
        settings,
        "TOKEN_COST_RATES",
        {
            "x" * 300: [9.0, 9.0],
            "bad key": [9.0, 9.0],
            "deepseek-chat": [0.01],
            "gpt-4": "0.5",
        },
    )
    monkeypatch.setattr(settings, "TOKEN_COST_DEFAULT_RATE", (0.0005, 0.002))
    cost = _estimate_cost("deepseek-chat", 1000, 0)
    assert cost == pytest.approx(0.0005), "非法条目必须跳过，只用合法兜底费率"


def test_estimate_cost_huge_integer_does_not_overflow(monkeypatch):
    """大整数：10**400 在 rate 或 token 数上都不能 OverflowError/500。"""
    from app.api.v1.endpoints.token_usage import _safe_rate

    assert _safe_rate([10**400, 0.001]) is None
    monkeypatch.setattr(settings, "TOKEN_COST_RATES", {"deepseek-chat": [10**400, 0.001]})
    monkeypatch.setattr(settings, "TOKEN_COST_DEFAULT_RATE", [10**400, 0.002])
    cost = _estimate_cost("deepseek-chat", 1000, 500)
    assert math.isfinite(cost) and cost >= 0
    huge = _estimate_cost("deepseek-chat", 10**400, 10**400)
    assert math.isfinite(huge) and huge >= 0


def test_cost_rate_config_rejects_huge_integer():
    """配置层：float(10**400) 抛可读 ValidationError，不 500。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="TOKEN_COST_RATES"):
        Settings(TOKEN_COST_RATES={"gpt-4": [10**400, 0.01]})
    with pytest.raises(ValidationError, match="TOKEN_COST_DEFAULT_RATE"):
        Settings(TOKEN_COST_DEFAULT_RATE=[10**400, 0.01])


@pytest.mark.asyncio
async def test_summary_feature_empty_string_keeps_legacy_json(usage_db):
    """feature="" 视为未过滤，summary JSON 不出现新维度键。"""
    factory, _db_gen = usage_db
    await _seed(factory)
    async with factory() as s:
        s.add(
            TokenUsageRecord(
                user_id=100,
                organization_id=11,
                model="deepseek-chat",
                source="rag_chat",
                input_tokens=1000,
                output_tokens=100,
                cost_usd=0.0007,
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await s.commit()

    from app.main import app

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = lambda: _user(999, 11, superuser=True)
    client = TestClient(app)
    try:
        resp = client.get("/api/v1/token-usage/summary?feature=")
        assert resp.status_code == 200, resp.text
        # 判别关键：feature="" 必须等同未过滤（否则旧行被 feature == "" 滤掉 → 0）。
        assert resp.json()["total_input_tokens"] == 1000
        assert set(resp.json()) == {
            "total_input_tokens",
            "total_output_tokens",
            "total_cost_usd",
            "total_requests",
            "by_model",
            "by_user",
            "by_day",
        }, "feature='' 不得让 legacy 响应多出新键"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
