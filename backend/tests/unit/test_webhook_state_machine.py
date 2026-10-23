"""Webhook 幂等 / 投递状态机 / outbox 领取单元测试（issue #478）。

SQLite 内存库 + 两个 session 顺序模拟并发 + 唯一约束兜底；``SKIP LOCKED``
无法在 SQLite 运行时验证，改为对 MySQL 方言编译断言。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.config import settings
from app.core.database import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookRule,
    WebhookSessionMode,
)
from app.services import webhook_service as svc

FERNET_KEY = Fernet.generate_key().decode()
MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _OKAgent:
    def __init__(self, chunks: tuple[str, ...] = ("Hello ", "world")):
        self.chunks = chunks
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield SimpleNamespace(type="chunk", content=chunk)
        yield SimpleNamespace(type="done", content="")


class _ErrorEventAgent:
    async def chat(self, **kwargs):
        yield SimpleNamespace(type="error", content="boom")
        if False:  # pragma: no cover - 让函数保持 async generator 形态
            yield


class _BoomAgent:
    async def chat(self, **kwargs):
        raise RuntimeError("llm down")
        if False:  # pragma: no cover
            yield


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", MAX_ATTEMPTS)
    monkeypatch.setattr(settings, "WEBHOOK_WORKER_POLL_SECONDS", 1)

    async def _noop_log_activity(**kwargs):
        return None

    # 审计走独立 AsyncSessionLocal（真实 MySQL）；单测只验证状态机，不写审计。
    monkeypatch.setattr(svc.audit_service, "log_activity", _noop_log_activity)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rule = WebhookRule(
            org_id=1,
            created_by=7,
            name="test-rule",
            secret_encrypted=svc.encrypt_secret(svc.generate_secret()),
            enabled=True,
            prompt_template="事件：\n{payload}",
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
    yield SimpleNamespace(factory=factory, rule=rule)
    await engine.dispose()


async def _add_delivery(
    env,
    *,
    key: str = "k1",
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.RECEIVED,
    attempts: int = 0,
    next_attempt_at: datetime | None = None,
    processing_started_at: datetime | None = None,
    payload: str = '{"event":"ping"}',
) -> str:
    async with env.factory() as session:
        delivery = WebhookDelivery(
            rule_id=env.rule.id,
            org_id=env.rule.org_id,
            idempotency_key=key,
            status=status,
            payload_hash="hash",
            payload=payload,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            processing_started_at=processing_started_at,
        )
        session.add(delivery)
        await session.commit()
        return delivery.id


async def _register(env, *, key: str = "k1", payload: str = '{"event":"ping"}'):
    async with env.factory() as session:
        rule = await session.get(WebhookRule, env.rule.id)
        return await svc.register_delivery(
            session,
            rule,
            idempotency_key=key,
            payload_text=payload,
            payload_hash="hash",
        )


async def _get_delivery(env, delivery_id: str) -> WebhookDelivery:
    async with env.factory() as session:
        return await session.get(WebhookDelivery, delivery_id)


async def _count(env, model) -> int:
    async with env.factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


# ── 幂等语义（issue §5.2）─────────────────────────────────────────────


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_new_delivery_received_202(self, env):
        result = await _register(env)
        assert (result.http_status, result.status, result.duplicate) == (202, "received", False)
        row = await _get_delivery(env, result.delivery_id)
        assert row.status == WebhookDeliveryStatus.RECEIVED
        assert row.org_id == env.rule.org_id
        assert row.attempts == 0

    @pytest.mark.asyncio
    async def test_duplicate_succeeded_returns_200(self, env):
        delivery_id = await _add_delivery(env, status=WebhookDeliveryStatus.SUCCEEDED)
        result = await _register(env)
        assert (result.http_status, result.status, result.duplicate) == (200, "succeeded", True)
        assert result.delivery_id == delivery_id
        assert await _count(env, WebhookDelivery) == 1

    @pytest.mark.asyncio
    async def test_duplicate_failed_retryable_not_reset(self, env):
        next_at = _now() + timedelta(seconds=60)
        delivery_id = await _add_delivery(
            env,
            status=WebhookDeliveryStatus.FAILED,
            attempts=1,
            next_attempt_at=next_at,
        )
        result = await _register(env)
        assert (result.http_status, result.status, result.duplicate) == (202, "failed", True)
        row = await _get_delivery(env, delivery_id)
        assert row.attempts == 1
        assert row.next_attempt_at is not None

    @pytest.mark.asyncio
    async def test_duplicate_failed_terminal_resets_and_requeues(self, env):
        delivery_id = await _add_delivery(
            env,
            status=WebhookDeliveryStatus.FAILED,
            attempts=MAX_ATTEMPTS,
            next_attempt_at=None,
        )
        result = await _register(env)
        assert (result.http_status, result.status, result.duplicate) == (202, "received", True)
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.RECEIVED
        assert row.attempts == 0
        assert row.next_attempt_at is None
        assert row.processing_started_at is None
        assert row.error is None
        assert await _count(env, WebhookDelivery) == 1

    @pytest.mark.asyncio
    async def test_duplicate_processing_returns_202(self, env):
        await _add_delivery(env, status=WebhookDeliveryStatus.PROCESSING)
        result = await _register(env)
        assert (result.http_status, result.status, result.duplicate) == (202, "processing", True)

    @pytest.mark.asyncio
    async def test_db_unique_constraint_is_final_arbiter(self, env):
        """两个 session 顺序插入同一 (rule_id, idempotency_key)：第二个必撞唯一约束。"""
        async with env.factory() as first:
            first.add(
                WebhookDelivery(
                    rule_id=env.rule.id,
                    org_id=env.rule.org_id,
                    idempotency_key="dup",
                    status=WebhookDeliveryStatus.RECEIVED,
                    payload_hash="h",
                    payload="{}",
                )
            )
            await first.commit()
        async with env.factory() as second:
            second.add(
                WebhookDelivery(
                    rule_id=env.rule.id,
                    org_id=env.rule.org_id,
                    idempotency_key="dup",
                    status=WebhookDeliveryStatus.RECEIVED,
                    payload_hash="h",
                    payload="{}",
                )
            )
            with pytest.raises(IntegrityError):
                await second.commit()
        assert await _count(env, WebhookDelivery) == 1


# ── 领取语句 / 状态机 ─────────────────────────────────────────────────


class TestClaim:
    def test_claim_stmt_uses_skip_locked_on_mysql(self):
        compiled = str(svc.build_claim_stmt(_now()).compile(dialect=mysql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in compiled

    @pytest.mark.asyncio
    async def test_received_is_claimed(self, env):
        await _add_delivery(env)
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 1
        )

    @pytest.mark.asyncio
    async def test_terminal_failed_not_claimed(self, env):
        await _add_delivery(
            env,
            status=WebhookDeliveryStatus.FAILED,
            attempts=MAX_ATTEMPTS,
            next_attempt_at=None,
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_failed_not_due_not_claimed(self, env):
        await _add_delivery(
            env,
            status=WebhookDeliveryStatus.FAILED,
            attempts=1,
            next_attempt_at=_now() + timedelta(seconds=60),
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_failed_due_is_claimed_and_attempts_increment(self, env):
        delivery_id = await _add_delivery(
            env,
            status=WebhookDeliveryStatus.FAILED,
            attempts=1,
            next_attempt_at=_now() - timedelta(seconds=1),
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 1
        )
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.SUCCEEDED
        assert row.attempts == 2

    @pytest.mark.asyncio
    async def test_processing_lease_expired_reclaimed(self, env):
        delivery_id = await _add_delivery(
            env,
            status=WebhookDeliveryStatus.PROCESSING,
            attempts=1,
            processing_started_at=_now() - timedelta(minutes=11),
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 1
        )
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.SUCCEEDED
        assert row.attempts == 2

    @pytest.mark.asyncio
    async def test_processing_recent_not_reclaimed(self, env):
        await _add_delivery(
            env,
            status=WebhookDeliveryStatus.PROCESSING,
            attempts=1,
            processing_started_at=_now() - timedelta(minutes=1),
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_OKAgent()
            )
            == 0
        )


# ── 处理成功/失败 ─────────────────────────────────────────────────────


class TestProcessing:
    @pytest.mark.asyncio
    async def test_success_creates_webhook_session_and_messages(self, env):
        delivery_id = await _add_delivery(env)
        agent = _OKAgent()
        assert (
            await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=agent)
            == 1
        )

        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.SUCCEEDED
        assert row.session_id
        assert row.processed_at is not None
        assert row.attempts == 1
        assert row.error is None

        async with env.factory() as session:
            chat_session = await session.get(ChatSession, row.session_id)
            assert chat_session.source == "webhook"
            assert chat_session.webhook_rule_id == env.rule.id
            assert chat_session.webhook_delivery_id == delivery_id
            assert chat_session.user_id == 7
            assert chat_session.organization_id == 1
            messages = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == row.session_id)
                        .order_by(ChatMessage.seq)
                    )
                )
                .scalars()
                .all()
            )
        assert [m.seq for m in messages] == [1, 2]
        assert "Hello world" in messages[1].content

        call = agent.calls[0]
        assert call["organization_id"] == 1
        assert call["user_id"] == 7
        assert call["session_id"] == row.session_id
        assert svc.PAYLOAD_OPEN in call["query"]
        assert '{"event": "ping"}' in call["query"]

    @pytest.mark.asyncio
    async def test_error_event_marks_failed_with_backoff(self, env):
        delivery_id = await _add_delivery(env)
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_ErrorEventAgent()
            )
            == 1
        )
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.FAILED
        assert row.attempts == 1
        assert row.error and "boom" in row.error
        assert row.next_attempt_at is not None
        delta = row.next_attempt_at - _now()
        assert timedelta(seconds=55) < delta < timedelta(seconds=65)  # 2^1*30s

    @pytest.mark.asyncio
    async def test_exception_marks_failed(self, env):
        delivery_id = await _add_delivery(env)
        assert (
            await svc.webhook_service.poll_once(
                session_factory=env.factory, agent_service=_BoomAgent()
            )
            == 1
        )
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.FAILED
        assert "llm down" in (row.error or "")

    @pytest.mark.asyncio
    async def test_three_failures_reach_terminal(self, env):
        delivery_id = await _add_delivery(env)
        agent = _BoomAgent()
        for expected_attempts in (1, 2, 3):
            await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=agent)
            row = await _get_delivery(env, delivery_id)
            assert row.attempts == expected_attempts
            if expected_attempts < MAX_ATTEMPTS:
                assert row.next_attempt_at is not None
                # 模拟时间到点：下一轮直接可领
                async with env.factory() as session:
                    current = await session.get(WebhookDelivery, delivery_id)
                    current.next_attempt_at = _now() - timedelta(seconds=1)
                    await session.commit()
            else:
                assert row.next_attempt_at is None
        # 终态不再被自动领取
        assert (
            await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=agent)
            == 0
        )

    @pytest.mark.asyncio
    async def test_retry_reuses_same_session(self, env):
        delivery_id = await _add_delivery(env)
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_BoomAgent())
        first = await _get_delivery(env, delivery_id)
        assert first.session_id
        async with env.factory() as session:
            current = await session.get(WebhookDelivery, delivery_id)
            current.next_attempt_at = _now() - timedelta(seconds=1)
            await session.commit()

        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        second = await _get_delivery(env, delivery_id)
        assert second.status == WebhookDeliveryStatus.SUCCEEDED
        assert second.session_id == first.session_id
        assert await _count(env, ChatSession) == 1

    @pytest.mark.asyncio
    async def test_invalid_payload_json_marks_failed(self, env):
        delivery_id = await _add_delivery(env, payload="{not json")
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        row = await _get_delivery(env, delivery_id)
        assert row.status == WebhookDeliveryStatus.FAILED
        assert "JSON" in (row.error or "")


# ── 会话模式（new / reuse）────────────────────────────────────────────


class TestSessionModes:
    async def _set_reuse(self, env):
        async with env.factory() as session:
            rule = await session.get(WebhookRule, env.rule.id)
            rule.session_mode = WebhookSessionMode.REUSE
            await session.commit()

    @pytest.mark.asyncio
    async def test_new_mode_creates_session_per_delivery(self, env):
        await _add_delivery(env, key="a", payload='{"event":"a"}')
        await _add_delivery(env, key="b", payload='{"event":"b"}')
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        assert await _count(env, ChatSession) == 2

    @pytest.mark.asyncio
    async def test_reuse_mode_reuses_session_by_key(self, env):
        await self._set_reuse(env)
        await _add_delivery(env, key="a", payload='{"session_key":"conv-1","n":1}')
        await _add_delivery(env, key="b", payload='{"session_key":"conv-1","n":2}')
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        async with env.factory() as session:
            sessions = (await session.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].webhook_session_key == "conv-1"
        assert await _count(env, ChatMessage) == 4

    @pytest.mark.asyncio
    async def test_reuse_without_session_key_falls_back_to_new(self, env):
        await self._set_reuse(env)
        await _add_delivery(env, key="a", payload='{"event":"a"}')
        await _add_delivery(env, key="b", payload='{"event":"b"}')
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        assert await _count(env, ChatSession) == 2

    @pytest.mark.asyncio
    async def test_reuse_session_scoped_to_rule_and_user(self, env):
        await self._set_reuse(env)
        async with env.factory() as session:
            other = WebhookRule(
                org_id=1,
                created_by=8,
                name="other",
                secret_encrypted=svc.encrypt_secret(svc.generate_secret()),
                enabled=True,
                prompt_template="{payload}",
                session_mode=WebhookSessionMode.REUSE,
            )
            session.add(other)
            await session.commit()
            other_id = other.id
        # 同 session_key 但不同规则 → 两个会话
        await _add_delivery(env, key="a", payload='{"session_key":"shared"}')
        async with env.factory() as session:
            session.add(
                WebhookDelivery(
                    rule_id=other_id,
                    org_id=1,
                    idempotency_key="a",
                    status=WebhookDeliveryStatus.RECEIVED,
                    payload_hash="h",
                    payload='{"session_key":"shared"}',
                )
            )
            await session.commit()
        await svc.webhook_service.poll_once(session_factory=env.factory, agent_service=_OKAgent())
        assert await _count(env, ChatSession) == 2
