"""Webhook API 测试（issue #478）：CRUD/权限/验签/body 上限/幂等/dry-run/轮换/限流。

SQLite 内存库 + ``dependency_overrides``（与 tests/api/test_scheduled_tasks_api.py
同型，不连真实服务）。413 用例用一个 ASGI 包一层 receive 计数器，证明
``Content-Length`` 超限时端点**没有**读取 ``request.stream()``。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.config import settings
from app.core.database import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookRule

BASE = "/api/v1/webhooks"
FERNET_KEY = Fernet.generate_key().decode()


def _make_user(uid=1, org=1, superuser=False):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=org,
        is_superuser=superuser,
        is_active=True,
        role="user",
    )


def _sign(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    raw_key = base64.b64decode(secret[len("whsec_") :])
    digest = hmac.new(
        raw_key, f"{message_id}.{timestamp}.".encode() + body, hashlib.sha256
    ).digest()
    return "v1," + base64.b64encode(digest).decode()


def _signed_headers(
    secret: str, body: bytes, *, message_id="msg_1", timestamp: int | None = None
) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "webhook-id": message_id,
        "webhook-timestamp": ts,
        "webhook-signature": _sign(secret, message_id, ts, body),
        "content-type": "application/json",
    }


class _ReceiveCounter:
    """统计 ASGI ``http.request`` 消息数——用来断言端点没读 body。"""

    def __init__(self, app):
        self.app = app
        self.request_messages = 0

    async def __call__(self, scope, receive, send):
        async def counting_receive():
            message = await receive()
            if message.get("type") == "http.request":
                self.request_messages += 1
            return message

        await self.app(scope, counting_receive, send)


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_BODY_BYTES", 1048576)
    monkeypatch.setattr(settings, "WEBHOOK_RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(settings, "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", 300)

    # 限流进程内兜底是模块级 dict：逐用例清空，避免 unknown:{ip} 桶跨用例污染。
    from app.services import webhook_service as _svc

    _svc._memory_rate.clear()

    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app
    from app.services.audit_service import audit_service

    async def _noop_log_activity(**kwargs):
        return None

    monkeypatch.setattr(audit_service, "log_activity", _noop_log_activity)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(user=_make_user(superuser=True))

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: state.user
    counter = _ReceiveCounter(app)
    transport = httpx.ASGITransport(app=counter, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, state=state, factory=factory, counter=counter)
    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_rule(api, **overrides) -> dict:
    body = {
        "name": "test hook",
        "prompt_template": "事件：\n{payload}",
        "enabled": True,
    }
    body.update(overrides)
    response = await api.client.post(f"{BASE}/rules", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _post_delivery(
    api,
    rule_id: str,
    secret: str,
    payload: dict | None = None,
    *,
    message_id: str = "msg_1",
    timestamp: int | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
):
    raw = body if body is not None else json.dumps(payload or {"event": "ping"}).encode()
    return await api.client.post(
        f"{BASE}/{rule_id}",
        content=raw,
        headers=headers
        if headers is not None
        else _signed_headers(secret, raw, message_id=message_id, timestamp=timestamp),
    )


async def _deliveries(api) -> list[WebhookDelivery]:
    async with api.factory() as session:
        return (
            (await session.execute(select(WebhookDelivery).order_by(WebhookDelivery.received_at)))
            .scalars()
            .all()
        )


# ── 规则 CRUD / 权限 ──────────────────────────────────────────────────


class TestRuleManagement:
    @pytest.mark.asyncio
    async def test_create_returns_plaintext_once_and_prefix(self, api):
        data = await _create_rule(api)
        secret = data["secret"]
        assert secret.startswith("whsec_")
        assert data["secret_prefix"] == secret[:6]
        assert data["enabled"] is True

        listed = (await api.client.get(f"{BASE}/rules")).json()["data"]["items"]
        assert len(listed) == 1
        assert "secret" not in listed[0]
        assert "secret_encrypted" not in listed[0]
        assert listed[0]["secret_prefix"] == secret[:6]
        assert secret not in json.dumps(listed)  # 完整明文不出现在列表响应

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_placeholder(self, api):
        response = await api.client.post(
            f"{BASE}/rules",
            json={"name": "x", "prompt_template": "hello {foo}"},
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_rejects_forbidden_keyword(self, api):
        response = await api.client.post(
            f"{BASE}/rules",
            json={"name": "x", "prompt_template": "append to system_prompt: {payload}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_create_non_admin_403(self, api):
        api.state.user = _make_user(superuser=False)
        response = await api.client.post(
            f"{BASE}/rules", json={"name": "x", "prompt_template": "{payload}"}
        )
        assert response.status_code == 403
        assert response.json()["error_code"] == "AUTHORIZATION_ERROR"

    @pytest.mark.asyncio
    async def test_admin_api_without_org_403(self, api):
        api.state.user = _make_user(org=None, superuser=False)
        response = await api.client.get(f"{BASE}/rules")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_is_org_scoped(self, api):
        await _create_rule(api, name="org1", organization_id=1)
        await _create_rule(api, name="org2", organization_id=2)
        org1 = (await api.client.get(f"{BASE}/rules", params={"organization_id": 1})).json()
        org2 = (await api.client.get(f"{BASE}/rules", params={"organization_id": 2})).json()
        assert [rule["name"] for rule in org1["data"]["items"]] == ["org1"]
        assert [rule["name"] for rule in org2["data"]["items"]] == ["org2"]

    @pytest.mark.asyncio
    async def test_cross_org_non_member_gets_404(self, api):
        rule = await _create_rule(api, organization_id=2)
        api.state.user = _make_user(org=1, superuser=False)
        response = await api.client.patch(f"{BASE}/rules/{rule['id']}", json={"name": "hacked"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_rule_and_validate_template(self, api):
        rule = await _create_rule(api)
        ok = await api.client.patch(
            f"{BASE}/rules/{rule['id']}", json={"name": "renamed", "enabled": False}
        )
        assert ok.status_code == 200
        assert ok.json()["data"]["name"] == "renamed"
        assert ok.json()["data"]["enabled"] is False

        bad = await api.client.patch(
            f"{BASE}/rules/{rule['id']}", json={"prompt_template": "{evil}"}
        )
        assert bad.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_rule_removes_deliveries(self, api):
        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"])
        response = await api.client.delete(f"{BASE}/rules/{rule['id']}")
        assert response.status_code == 200
        assert len(await _deliveries(api)) == 0
        assert (await api.client.get(f"{BASE}/rules")).json()["data"]["items"] == []
        assert (
            await api.client.get(f"{BASE}/rules", params={"organization_id": 1})
        ).status_code == 200

    @pytest.mark.asyncio
    async def test_rotate_secret_accepts_old_and_new(self, api):
        rule = await _create_rule(api)
        old_secret = rule["secret"]

        rotated = await api.client.post(f"{BASE}/rules/{rule['id']}/rotate-secret")
        assert rotated.status_code == 200
        new_secret = rotated.json()["data"]["secret"]
        assert new_secret != old_secret
        assert rotated.json()["data"]["secret_rotated_at"] is not None

        # 轮换期 24h 内新旧都接受
        assert (
            await _post_delivery(api, rule["id"], old_secret, message_id="old")
        ).status_code == 202
        assert (
            await _post_delivery(api, rule["id"], new_secret, message_id="new")
        ).status_code == 202

        listed = (await api.client.get(f"{BASE}/rules")).json()["data"]["items"]
        assert new_secret not in json.dumps(listed)
        assert old_secret not in json.dumps(listed)


# ── 入站验签 / 状态码 ────────────────────────────────────────────────


class TestInbound:
    @pytest.mark.asyncio
    async def test_valid_signature_202_and_received_row(self, api):
        rule = await _create_rule(api)
        response = await _post_delivery(api, rule["id"], rule["secret"])
        assert response.status_code == 202, response.text
        data = response.json()["data"]
        assert data["status"] == "received"
        assert "duplicate" not in data
        rows = await _deliveries(api)
        assert len(rows) == 1
        assert rows[0].id == data["delivery_id"]
        assert rows[0].status == WebhookDeliveryStatus.RECEIVED

    @pytest.mark.asyncio
    async def test_invalid_signature_401_zero_rows(self, api):
        rule = await _create_rule(api)
        response = await _post_delivery(
            api, rule["id"], "whsec_" + base64.b64encode(b"x" * 32).decode()
        )
        assert response.status_code == 401
        assert response.json()["error_code"] == "AUTHENTICATION_ERROR"
        assert await _deliveries(api) == []

    @pytest.mark.asyncio
    async def test_expired_timestamp_401(self, api):
        rule = await _create_rule(api)
        response = await _post_delivery(
            api, rule["id"], rule["secret"], timestamp=int(time.time()) - 301
        )
        assert response.status_code == 401
        assert await _deliveries(api) == []

    @pytest.mark.asyncio
    async def test_disabled_rule_404_same_as_missing(self, api):
        rule = await _create_rule(api, enabled=False)
        disabled = await _post_delivery(api, rule["id"], rule["secret"])
        missing = await _post_delivery(api, "00000000-0000-0000-0000-000000000000", rule["secret"])
        assert disabled.status_code == missing.status_code == 404
        assert disabled.json()["message"] == missing.json()["message"]

    @pytest.mark.asyncio
    async def test_global_disabled_404(self, api, monkeypatch):
        rule = await _create_rule(api)
        monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)
        response = await _post_delivery(api, rule["id"], rule["secret"])
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_rule_limited_via_unknown_bucket(self, api, monkeypatch):
        """未知 rule_id 也用 ``unknown:{ip}`` 桶计数，不能靠随机 id 绕过限流。"""
        monkeypatch.setattr(settings, "WEBHOOK_RATE_LIMIT_PER_MINUTE", 2)
        wrong = "whsec_" + base64.b64encode(b"z" * 32).decode()
        unknown = "00000000-0000-0000-0000-000000000123"
        first = await _post_delivery(api, unknown, wrong)
        second = await _post_delivery(api, unknown, wrong)
        blocked = await _post_delivery(api, unknown, wrong)
        assert first.status_code == second.status_code == 404
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1

    @pytest.mark.asyncio
    async def test_body_too_large_413_without_reading_stream(self, api):
        rule = await _create_rule(api, max_body_bytes=16)
        body = b'{"event":"' + b"x" * 100 + b'"}'
        api.counter.request_messages = 0  # 只统计本次投递请求
        response = await _post_delivery(api, rule["id"], rule["secret"], body=body)
        assert response.status_code == 413
        payload = response.json()
        assert payload["error_code"] == "PAYLOAD_TOO_LARGE"
        assert payload["detail"]["max_body_bytes"] == 16
        assert api.counter.request_messages == 0, "Content-Length 超限时不得读 request.stream()"
        assert await _deliveries(api) == []


# ── 幂等 ─────────────────────────────────────────────────────────────


class TestIdempotencyAPI:
    @pytest.mark.asyncio
    async def test_duplicate_received_then_succeeded(self, api):
        rule = await _create_rule(api)
        first = await _post_delivery(api, rule["id"], rule["secret"], message_id="m1")
        second = await _post_delivery(api, rule["id"], rule["secret"], message_id="m1")
        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["data"]["duplicate"] is True
        rows = await _deliveries(api)
        assert len(rows) == 1

        # 模拟 poller 完成
        async with api.factory() as session:
            row = await session.get(WebhookDelivery, rows[0].id)
            row.status = WebhookDeliveryStatus.SUCCEEDED
            await session.commit()

        third = await _post_delivery(api, rule["id"], rule["secret"], message_id="m1")
        assert third.status_code == 200
        assert third.json()["data"]["status"] == "succeeded"
        assert third.json()["data"]["duplicate"] is True
        assert len(await _deliveries(api)) == 1

    @pytest.mark.asyncio
    async def test_missing_webhook_id_is_401(self, api):
        """Standard Webhooks 的签名内容包含 webhook-id，缺失即无法验签（401）。

        body-hash 幂等兜底只适用于非 Standard 提供方（provider=github 预留）；
        纯函数层已由 test_webhook_signature 覆盖。
        """
        rule = await _create_rule(api)
        body = b'{"event":"same"}'
        headers = _signed_headers(rule["secret"], body, message_id="m1")
        headers.pop("webhook-id")
        response = await api.client.post(f"{BASE}/{rule['id']}", content=body, headers=headers)
        assert response.status_code == 401
        assert await _deliveries(api) == []

    @pytest.mark.asyncio
    async def test_terminal_failed_redelivery_resets(self, api):
        rule = await _create_rule(api)
        first = await _post_delivery(api, rule["id"], rule["secret"], message_id="m1")
        delivery_id = first.json()["data"]["delivery_id"]
        async with api.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            row.status = WebhookDeliveryStatus.FAILED
            row.attempts = 3
            row.next_attempt_at = None
            await session.commit()

        response = await _post_delivery(api, rule["id"], rule["secret"], message_id="m1")
        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] == "received"
        assert data["duplicate"] is True
        async with api.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            assert (row.status, row.attempts, row.next_attempt_at) == (
                WebhookDeliveryStatus.RECEIVED,
                0,
                None,
            )

    @pytest.mark.asyncio
    async def test_rate_limit_429_retry_after_and_no_extra_row(self, api):
        rule = await _create_rule(api, rate_limit_per_minute=2)
        for index in range(2):
            response = await _post_delivery(api, rule["id"], rule["secret"], message_id=f"m{index}")
            assert response.status_code == 202
        blocked = await _post_delivery(api, rule["id"], rule["secret"], message_id="m2")
        assert blocked.status_code == 429
        assert blocked.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert len(await _deliveries(api)) == 2

    @pytest.mark.asyncio
    async def test_bad_signature_consumes_rate_limit_then_429(self, api):
        """issue #497：限流先于验签——坏签名不是免死金牌，超限即 429。"""
        rule = await _create_rule(api, rate_limit_per_minute=2)
        wrong = "whsec_" + base64.b64encode(b"x" * 32).decode()
        first = await _post_delivery(api, rule["id"], wrong, message_id="bad-1")
        second = await _post_delivery(api, rule["id"], wrong, message_id="bad-2")
        assert first.status_code == second.status_code == 401
        blocked = await _post_delivery(api, rule["id"], wrong, message_id="bad-3")
        assert blocked.status_code == 429
        assert blocked.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert await _deliveries(api) == []


# ── dry-run ──────────────────────────────────────────────────────────


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_without_signature_headers(self, api):
        rule = await _create_rule(api)
        response = await api.client.post(
            f"{BASE}/rules/{rule['id']}/test", json={"payload": {"event": "ping"}}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["signature_valid"] is None
        assert "<<<UNTRUSTED_WEBHOOK_PAYLOAD>>>" in data["rendered_prompt"]
        assert '{"event": "ping"}' in data["rendered_prompt"]
        assert await _deliveries(api) == []

    @pytest.mark.asyncio
    async def test_dry_run_with_valid_signature(self, api):
        rule = await _create_rule(api)
        payload = {"event": "ping"}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        headers = _signed_headers(rule["secret"], raw)
        response = await api.client.post(
            f"{BASE}/rules/{rule['id']}/test",
            json={"payload": payload, "signature_headers": headers},
        )
        assert response.status_code == 200
        assert response.json()["data"]["signature_valid"] is True
        assert await _deliveries(api) == []

    @pytest.mark.asyncio
    async def test_dry_run_with_invalid_signature_reports_false(self, api):
        rule = await _create_rule(api)
        payload = {"event": "ping"}
        headers = _signed_headers(rule["secret"], b"different-body")
        response = await api.client.post(
            f"{BASE}/rules/{rule['id']}/test",
            json={"payload": payload, "signature_headers": headers},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["signature_valid"] is False
        assert data["signature_error"]
        assert await _deliveries(api) == []


# ── 投递列表 ─────────────────────────────────────────────────────────


class TestDeliveryList:
    @pytest.mark.asyncio
    async def test_filters_by_rule_and_status(self, api):
        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"], message_id="a")
        await _post_delivery(api, rule["id"], rule["secret"], message_id="b")
        listing = await api.client.get(f"{BASE}/deliveries", params={"rule_id": rule["id"]})
        assert listing.status_code == 200
        items = listing.json()["data"]["items"]
        assert len(items) == 2
        assert all(item["status"] == "received" for item in items)

        succeeded = await api.client.get(
            f"{BASE}/deliveries", params={"rule_id": rule["id"], "status": "succeeded"}
        )
        assert succeeded.json()["data"]["items"] == []

        invalid = await api.client.get(f"{BASE}/deliveries", params={"status": "bogus"})
        assert invalid.status_code == 400

    @pytest.mark.asyncio
    async def test_deliveries_org_scoped(self, api):
        rule1 = await _create_rule(api, organization_id=1)
        rule2 = await _create_rule(api, organization_id=2)
        await _post_delivery(api, rule1["id"], rule1["secret"])
        await _post_delivery(api, rule2["id"], rule2["secret"])
        org1 = await api.client.get(f"{BASE}/deliveries", params={"organization_id": 1})
        assert len(org1.json()["data"]["items"]) == 1
        org2 = await api.client.get(f"{BASE}/deliveries", params={"organization_id": 2})
        assert len(org2.json()["data"]["items"]) == 1


# ── 运行时门控（issue #497：未启用/缺 key 一律 503，不 500、不泄堆栈）──


class TestRuntimeGating:
    @pytest.mark.asyncio
    async def test_admin_api_disabled_503_without_leaks(self, api, monkeypatch):
        rule = await _create_rule(api)
        monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)

        responses = [
            await api.client.post(
                f"{BASE}/rules", json={"name": "x", "prompt_template": "{payload}"}
            ),
            await api.client.patch(f"{BASE}/rules/{rule['id']}", json={"name": "x"}),
            await api.client.delete(f"{BASE}/rules/{rule['id']}"),
            await api.client.post(f"{BASE}/rules/{rule['id']}/rotate-secret"),
            await api.client.post(f"{BASE}/rules/{rule['id']}/test", json={"payload": {}}),
            await api.client.get(f"{BASE}/rules"),
        ]
        for response in responses:
            assert response.status_code == 503, response.text
            assert response.json()["error_code"] == "WEBHOOKS_DISABLED"
            text = response.text
            assert "Traceback" not in text
            assert "webhook_service.py" not in text
            assert "site-packages" not in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_key", ["", "not-a-fernet-key"])
    async def test_admin_api_key_missing_or_invalid_503(self, api, monkeypatch, bad_key):
        monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", bad_key)
        response = await api.client.post(
            f"{BASE}/rules", json={"name": "x", "prompt_template": "{payload}"}
        )
        assert response.status_code == 503, response.text
        assert response.json()["error_code"] == "WEBHOOK_KEY_MISSING"
        text = response.text
        assert "Traceback" not in text
        assert "Fernet" not in text
        assert "site-packages" not in text

    @pytest.mark.asyncio
    async def test_public_endpoint_key_missing_404(self, api, monkeypatch):
        rule = await _create_rule(api)
        monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", "")
        response = await _post_delivery(api, rule["id"], rule["secret"])
        assert response.status_code == 404
        assert await _deliveries(api) == []


# ── reuse 历史 / 租约 fencing（issue #497）───────────────────────────


class _RecordingAgent:
    def __init__(self, chunks: tuple[str, ...] = ("new answer",)):
        self.chunks = chunks
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield SimpleNamespace(type="chunk", content=chunk)
        yield SimpleNamespace(type="done", content="")


class TestReuseHistory:
    @pytest.mark.asyncio
    async def test_reuse_delivery_carries_recent_history(self, api):
        from app.services import webhook_service as svc

        rule = await _create_rule(api, session_mode="reuse")
        async with api.factory() as session:
            session.add(
                ChatSession(
                    id="sess-1",
                    user_id=1,
                    organization_id=1,
                    title="[webhook] test hook",
                    status=ChatSessionStatus.ACTIVE,
                    root_session_id="sess-1",
                    source="webhook",
                    webhook_rule_id=rule["id"],
                    webhook_session_key="conv-1",
                )
            )
            session.add_all(
                [
                    ChatMessage(
                        id="m1",
                        session_id="sess-1",
                        seq=1,
                        content="old question",
                        message_type=MessageType.USER,
                    ),
                    ChatMessage(
                        id="m2",
                        session_id="sess-1",
                        seq=2,
                        content="old answer",
                        message_type=MessageType.ASSISTANT,
                    ),
                ]
            )
            await session.commit()

        response = await _post_delivery(
            api,
            rule["id"],
            rule["secret"],
            payload={"session_key": "conv-1", "event": "ping"},
            message_id="reuse-1",
        )
        assert response.status_code == 202

        agent = _RecordingAgent()
        assert (
            await svc.webhook_service.poll_once(session_factory=api.factory, agent_service=agent)
            == 1
        )
        history = agent.calls[0]["history"]
        assert len(history) > 1, "reuse 模式必须携带会话历史（长度 > 1）"
        assert history == [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]

        async with api.factory() as session:
            messages = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == "sess-1")
                        .order_by(ChatMessage.seq)
                    )
                )
                .scalars()
                .all()
            )
        # 历史 2 条 + 本轮 user/assistant 2 条，且落在同一会话
        assert len(messages) == 4
        assert messages[-1].content == "new answer"

    @pytest.mark.asyncio
    async def test_reuse_without_session_key_marks_fallback_and_keeps_marker(self, api):
        from app.services import webhook_service as svc

        rule = await _create_rule(api, session_mode="reuse")
        await _post_delivery(
            api, rule["id"], rule["secret"], payload={"event": "ping"}, message_id="fb-1"
        )
        assert (
            await svc.webhook_service.poll_once(
                session_factory=api.factory, agent_service=_RecordingAgent()
            )
            == 1
        )
        rows = await _deliveries(api)
        assert len(rows) == 1
        assert rows[0].status == WebhookDeliveryStatus.SUCCEEDED
        assert (rows[0].error or "").startswith("reuse_fallback=true")


class TestLeaseFencing:
    @pytest.mark.asyncio
    async def test_claim_rotates_token_and_increments_lease_version(self, api):
        from app.services import webhook_service as svc

        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"], message_id="rot-1")
        assert (
            await svc.webhook_service.poll_once(
                session_factory=api.factory, agent_service=_RecordingAgent()
            )
            == 1
        )
        rows = await _deliveries(api)
        assert rows[0].status == WebhookDeliveryStatus.SUCCEEDED
        assert rows[0].lease_version == 1
        assert rows[0].processing_token  # 领取时写入的租约 token

    @pytest.mark.asyncio
    async def test_stale_worker_writeback_is_fenced_out(self, api):
        """模拟超时重领：旧 token 的完成写回 rowcount 0，绝不双写消息。

        「它能红吗」：去掉 ``_complete_delivery`` 的 ``processing_token`` 条件，
        本用例会因为状态被改成 succeeded、并写出 2 条消息而变红。
        """
        from app.services import webhook_service as svc

        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"], message_id="fence-1")
        rows = await _deliveries(api)
        delivery_id = rows[0].id

        # worker A 已领取（token-A）并开始处理
        async with api.factory() as session:
            await session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .values(
                    status=WebhookDeliveryStatus.PROCESSING,
                    processing_token="token-A",
                    lease_version=1,
                    processing_started_at=svc._utcnow(),
                    attempts=1,
                )
            )
            await session.commit()

        class _StealingAgent:
            """聊天期间租赁超时被 worker B 重领：token-A → token-B。"""

            def __init__(self, factory):
                self.factory = factory

            async def chat(self, **kwargs):
                async with self.factory() as session:
                    await session.execute(
                        update(WebhookDelivery)
                        .where(WebhookDelivery.id == delivery_id)
                        .values(processing_token="token-B", lease_version=2)
                    )
                    await session.commit()
                yield SimpleNamespace(type="chunk", content="late answer")
                yield SimpleNamespace(type="done", content="")

        await svc.webhook_service.process_delivery(
            delivery_id,
            processing_token="token-A",
            session_factory=api.factory,
            agent_service=_StealingAgent(api.factory),
        )

        async with api.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
            messages = (await session.execute(select(ChatMessage))).scalars().all()
        assert row.status == WebhookDeliveryStatus.PROCESSING  # 新租约仍在处理
        assert row.processing_token == "token-B"
        assert row.lease_version == 2
        assert row.processed_at is None
        assert messages == [], "迟到 worker 不得写任何消息（防双写）"

    @pytest.mark.asyncio
    async def test_session_writeback_requires_lease_token(self, api):
        """会话/session_id 写回也 fencing：旧 token 不得覆盖或新建空会话。"""
        from app.services import webhook_service as svc

        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"], message_id="fence-3")
        rows = await _deliveries(api)
        delivery_id = rows[0].id

        async with api.factory() as session:
            await session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .values(
                    status=WebhookDeliveryStatus.PROCESSING,
                    processing_token="token-B",
                    lease_version=1,
                    processing_started_at=svc._utcnow(),
                    attempts=1,
                )
            )
            await session.commit()
            delivery = await session.get(WebhookDelivery, delivery_id)
            rule_row = await session.get(WebhookRule, rule["id"])
            with pytest.raises(svc.WebhookDeliveryError):
                await svc.webhook_service._ensure_session(
                    session, delivery, rule_row, {"event": "x"}, "token-A"
                )

        async with api.factory() as session:
            sessions = (await session.execute(select(ChatSession))).scalars().all()
            row = await session.get(WebhookDelivery, delivery_id)
        assert sessions == [], "旧 token 不得新建会话"
        assert row.session_id is None
        assert row.processing_token == "token-B"

    @pytest.mark.asyncio
    async def test_stale_worker_failure_writeback_is_fenced_out(self, api):
        """旧 token 的失败写回同样落空：不得把新 worker 的 processing 改成 failed。"""
        from app.services import webhook_service as svc

        rule = await _create_rule(api)
        await _post_delivery(api, rule["id"], rule["secret"], message_id="fence-2")
        rows = await _deliveries(api)
        delivery_id = rows[0].id

        async with api.factory() as session:
            await session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .values(
                    status=WebhookDeliveryStatus.PROCESSING,
                    processing_token="token-B",
                    lease_version=2,
                    processing_started_at=svc._utcnow(),
                    attempts=1,
                )
            )
            await session.commit()

        await svc.webhook_service._mark_failed(api.factory, delivery_id, "token-A", "stale failure")
        async with api.factory() as session:
            row = await session.get(WebhookDelivery, delivery_id)
        assert row.status == WebhookDeliveryStatus.PROCESSING
        assert row.processing_token == "token-B"
        assert row.error is None


# ── 审计：规则变更写 user_activity_logs（真服务依赖）─────────────────


@pytest.mark.asyncio
async def test_rule_creation_audit_kwargs(api, monkeypatch):
    """审计调用参数正确（audit_service 在 fixture 里被替换为 no-op）。"""
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    from app.services.audit_service import audit_service

    monkeypatch.setattr(audit_service, "log_activity", _capture)
    rule = await _create_rule(api)
    assert calls and calls[0]["action"] == "create_webhook_rule"
    assert calls[0]["target_id"] == rule["id"]
    assert "secret" not in json.dumps(calls[0]["detail"])
