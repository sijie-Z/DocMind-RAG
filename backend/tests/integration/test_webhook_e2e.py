"""Webhook e2e（issue #478）：签名投递 → 202 → poll_once → 会话落库。

RUN_INTEGRATION=1 + 真实 MySQL（临时库，用后即删）；LLM 用 fake agent 替代，
其余链路（HTTP 端点 / 幂等 / outbox 状态机 / ChatSession / ChatMessage）全真。
Redis 不可用时幂等自动降级到 DB 唯一约束（本用例不依赖 Redis）。

第二个用例是 blocker B1/B2 的 MySQL 回归：合法签名、未超全局上限（1 MiB）但
**超过 64 KiB** 的 body，必须能存进 `webhook_deliveries.payload` 与
`chat_messages.content`（MEDIUMTEXT），不能 DataError。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # 注册全部表
from app.core.config import settings
from app.core.database import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.organization import Organization
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookRule
from app.services import webhook_service as svc

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1，并确保可达）",
)

BASE = "/api/v1/webhooks"
FERNET_KEY = Fernet.generate_key().decode()


def _mysql_reachable() -> bool:
    raw = settings.DATABASE_URL.split("://", 1)[-1]
    parsed = urlparse("//" + raw)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 3306), timeout=2):
            return True
    except OSError:
        return False


if not _mysql_reachable():
    pytestmark = [pytestmark, pytest.mark.skip(reason="MySQL 不可达")]


class _FakeAgent:
    async def chat(self, **kwargs):
        yield SimpleNamespace(type="chunk", content="pong from agent")
        yield SimpleNamespace(type="done", content="")


def _sign(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    raw_key = base64.b64decode(secret[len("whsec_") :])
    digest = hmac.new(
        raw_key, f"{message_id}.{timestamp}.".encode() + body, hashlib.sha256
    ).digest()
    return "v1," + base64.b64encode(digest).decode()


def _headers(secret: str, body: bytes, message_id: str | None = None) -> dict[str, str]:
    msg_id = message_id or f"msg_{uuid.uuid4().hex}"
    timestamp = str(int(time.time()))
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": _sign(secret, msg_id, timestamp, body),
        "content-type": "application/json",
    }


@asynccontextmanager
async def _temp_mysql_stack(monkeypatch):
    """临时 MySQL 库 + 用户/组织/规则；退出时删库。"""
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "WEBHOOK_WORKER_POLL_SECONDS", 1)

    from app.services.audit_service import audit_service

    async def _noop_log_activity(**kwargs):
        return None

    monkeypatch.setattr(audit_service, "log_activity", _noop_log_activity)

    base_url = settings.DATABASE_URL
    tmp_db = f"docmind_478_e2e_{uuid.uuid4().hex[:8]}"
    tmp_url = base_url.rsplit("/", 1)[0] + "/" + tmp_db
    admin_engine = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"CREATE DATABASE {tmp_db} CHARACTER SET utf8mb4"))
        engine = create_async_engine(tmp_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        secret_plain = svc.generate_secret()
        async with factory() as session:
            user = User(
                username=f"wh-e2e-{uuid.uuid4().hex[:8]}",
                email=f"{uuid.uuid4().hex[:8]}@example.com",
                hashed_password="x",
                is_superuser=False,
            )
            session.add(user)
            await session.flush()
            org = Organization(name=f"wh-e2e-org-{uuid.uuid4().hex[:8]}", owner_id=user.id)
            session.add(org)
            await session.flush()
            user.organization_id = org.id
            rule = WebhookRule(
                org_id=org.id,
                created_by=user.id,
                name="e2e rule",
                secret_encrypted=svc.encrypt_secret(secret_plain),
                enabled=True,
                prompt_template="处理：\n{payload}",
            )
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            rule_id, user_id, org_id = rule.id, user.id, org.id
        yield SimpleNamespace(
            factory=factory,
            engine=engine,
            rule_id=rule_id,
            user_id=user_id,
            org_id=org_id,
            secret=secret_plain,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {tmp_db}"))
        await admin_engine.dispose()


@asynccontextmanager
async def _client(factory):
    from app.core.database import get_db
    from app.main import app

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_signed_delivery_end_to_end(monkeypatch):
    async with _temp_mysql_stack(monkeypatch) as stack, _client(stack.factory) as client:
        body = json.dumps({"event": "ping", "n": 1}).encode()
        headers = _headers(stack.secret, body)
        response = await client.post(f"{BASE}/{stack.rule_id}", content=body, headers=headers)
        assert response.status_code == 202, response.text
        delivery_id = response.json()["data"]["delivery_id"]

        # poll 前：received，无会话
        async with stack.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            assert row.status == WebhookDeliveryStatus.RECEIVED
        async with stack.factory() as session:
            assert (await session.execute(select(ChatSession))).scalars().all() == []

        claimed = await svc.webhook_service.poll_once(
            session_factory=stack.factory, agent_service=_FakeAgent()
        )
        assert claimed == 1

        async with stack.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            assert row.status == WebhookDeliveryStatus.SUCCEEDED
            assert row.session_id
            assert row.attempts == 1
            # issue #497：领取轮换租约 token 并递增代数（fencing 列真实落库）。
            assert row.lease_version == 1
            assert row.processing_token
            chat_session = await session.get(ChatSession, row.session_id)
            assert chat_session.source == "webhook"
            assert chat_session.webhook_rule_id == stack.rule_id
            assert chat_session.webhook_delivery_id == delivery_id
            assert chat_session.user_id == stack.user_id
            assert chat_session.organization_id == stack.org_id
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
            assert [message.seq for message in messages] == [1, 2]
            assert "pong from agent" in messages[1].content

        # 同一 webhook-id 重投：200 duplicate，不重复建会话
        duplicate = await client.post(f"{BASE}/{stack.rule_id}", content=body, headers=headers)
        assert duplicate.status_code == 200
        assert duplicate.json()["data"]["duplicate"] is True
        async with stack.factory() as session:
            sessions = (await session.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 1


@pytest.mark.asyncio
async def test_large_signed_payload_succeeds_end_to_end(monkeypatch):
    """> 64 KiB 且 < 1 MiB 的合法签名 body：B1/B2 回归（TEXT → MEDIUMTEXT）。"""
    async with _temp_mysql_stack(monkeypatch) as stack, _client(stack.factory) as client:
        blob = "x" * 100_000
        body = json.dumps({"event": "big", "blob": blob}).encode()
        assert len(body) > 65_535

        response = await client.post(
            f"{BASE}/{stack.rule_id}", content=body, headers=_headers(stack.secret, body)
        )
        assert response.status_code == 202, response.text
        delivery_id = response.json()["data"]["delivery_id"]

        async with stack.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            assert len(row.payload) == len(body.decode())

        assert (
            await svc.webhook_service.poll_once(
                session_factory=stack.factory, agent_service=_FakeAgent()
            )
            == 1
        )

        async with stack.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            assert row.status == WebhookDeliveryStatus.SUCCEEDED
            user_message = (
                (
                    await session.execute(
                        select(ChatMessage).where(
                            ChatMessage.session_id == row.session_id, ChatMessage.seq == 1
                        )
                    )
                )
                .scalars()
                .one()
            )
            assert len(user_message.content) > 65_535

        # 直接钉住三列在 MySQL 上是 MEDIUMTEXT（create_all 路径）。
        async with stack.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT table_name, column_name, column_type FROM "
                        "information_schema.columns WHERE table_schema = DATABASE() AND ("
                        "(table_name='webhook_deliveries' AND column_name='payload') OR "
                        "(table_name='chat_messages' AND column_name='content') OR "
                        "(table_name='agent_tasks' AND column_name='query'))"
                    )
                )
            ).all()
        types = {(table, column): column_type for table, column, column_type in rows}
        assert set(types) == {
            ("webhook_deliveries", "payload"),
            ("chat_messages", "content"),
            ("agent_tasks", "query"),
        }
        assert set(types.values()) == {"mediumtext"}, types


@pytest.mark.asyncio
async def test_webhook_disabled_rejects_delivery(monkeypatch):
    """WEBHOOKS_ENABLED=false → 入站 404（不接收）。不需要真实 DB：规则加载前即拒绝。"""
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)
    from app.main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"{BASE}/00000000-0000-0000-0000-000000000000", content=b"{}")
    assert response.status_code == 404
