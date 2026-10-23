"""Webhook 验签 / Fernet / 模板注入防护 / body 上限单元测试（issue #478）。

只依赖标准库 + 服务层纯函数；DB/HTTP 由 state machine 与 API 测试覆盖。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.exceptions import (
    AuthenticationError,
    PayloadTooLargeError,
    RateLimitError,
    ValidationError,
)
from app.services.webhook_service import (
    PAYLOAD_CLOSE,
    PAYLOAD_OPEN,
    WebhookEncryptionError,
    active_secrets,
    decrypt_secret,
    derive_idempotency_key,
    encrypt_secret,
    enforce_rate_limit,
    generate_secret,
    read_body_with_limit,
    render_prompt,
    secret_prefix,
    validate_prompt_template,
    verify_signature,
)

FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.setattr(settings, "WEBHOOK_RATE_LIMIT_PER_MINUTE", 60)


def _new_secret() -> str:
    return generate_secret()


def _sign(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    raw_key = base64.b64decode(secret[len("whsec_") :])
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(raw_key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def _headers(secret: str, body: bytes, *, message_id="msg_1", timestamp=None) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "webhook-id": message_id,
        "webhook-timestamp": ts,
        "webhook-signature": _sign(secret, message_id, ts, body),
    }


def _rule(**overrides) -> SimpleNamespace:
    current = overrides.pop("secret", _new_secret())
    previous = overrides.pop("prev_secret", None)
    data = {
        "id": "rule-1",
        "secret_encrypted": encrypt_secret(current),
        "secret_prev_encrypted": encrypt_secret(previous) if previous else None,
        "secret_rotated_at": None,
        "prompt_template": "处理事件：{payload}\nrule={rule_id} delivery={delivery_id}",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


# ── Fernet ───────────────────────────────────────────────────────────


class TestFernet:
    def test_round_trip(self):
        plain = generate_secret()
        token = encrypt_secret(plain)
        assert token != plain
        assert decrypt_secret(token) == plain

    def test_secret_format(self):
        secret = generate_secret()
        assert secret.startswith("whsec_")
        # whsec_ + base64(32 bytes) == 44 chars
        assert len(secret) == 6 + 44
        assert base64.b64decode(secret[6:], validate=True)

    def test_secret_prefix_is_first_six(self):
        secret = generate_secret()
        assert secret_prefix(secret) == secret[:6] == "whsec_"

    def test_missing_key_raises_explicitly(self, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", "")
        with pytest.raises(WebhookEncryptionError):
            encrypt_secret("whsec_x")

    def test_wrong_key_decrypt_raises_explicitly(self, monkeypatch):
        token = encrypt_secret(generate_secret())
        monkeypatch.setattr(settings, "WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(WebhookEncryptionError):
            decrypt_secret(token)


# ── Standard Webhooks 验签 ───────────────────────────────────────────


class TestVerifySignature:
    def test_valid_signature_passes(self):
        secret = _new_secret()
        body = b'{"event":"ping"}'
        verify_signature(body, _headers(secret, body), [secret])

    def test_wrong_body_rejected(self):
        secret = _new_secret()
        headers = _headers(secret, b'{"event":"ping"}')
        with pytest.raises(AuthenticationError):
            verify_signature(b'{"event":"pong"}', headers, [secret])

    def test_wrong_secret_rejected(self):
        signer, verifier = _new_secret(), _new_secret()
        body = b"{}"
        with pytest.raises(AuthenticationError):
            verify_signature(body, _headers(signer, body), [verifier])

    def test_expired_timestamp_301s_rejected(self):
        secret = _new_secret()
        body = b"{}"
        headers = _headers(secret, body, timestamp=int(time.time()) - 301)
        with pytest.raises(AuthenticationError):
            verify_signature(body, headers, [secret], tolerance=300)

    def test_future_timestamp_301s_rejected(self):
        secret = _new_secret()
        body = b"{}"
        headers = _headers(secret, body, timestamp=int(time.time()) + 301)
        with pytest.raises(AuthenticationError):
            verify_signature(body, headers, [secret], tolerance=300)

    def test_timestamp_inside_window_passes(self):
        secret = _new_secret()
        body = b"{}"
        for offset in (-299, 299):
            headers = _headers(secret, body, timestamp=int(time.time()) + offset)
            verify_signature(body, headers, [secret], tolerance=300)

    def test_missing_headers_rejected(self):
        secret = _new_secret()
        for missing in ("webhook-id", "webhook-timestamp", "webhook-signature"):
            headers = _headers(secret, b"{}")
            headers.pop(missing)
            with pytest.raises(AuthenticationError):
                verify_signature(b"{}", headers, [secret])

    def test_multiple_signatures_any_match_passes(self):
        signer, verifier = _new_secret(), _new_secret()
        body = b"{}"
        headers = _headers(signer, body)
        wrong = _sign(verifier, "msg_1", headers["webhook-timestamp"], body)
        headers["webhook-signature"] = f"{wrong} {headers['webhook-signature']}"
        verify_signature(body, headers, [signer])

    def test_non_v1_versions_ignored(self):
        secret = _new_secret()
        body = b"{}"
        headers = _headers(secret, body)
        headers["webhook-signature"] = "v2,abc " + headers["webhook-signature"]
        verify_signature(body, headers, [secret])
        headers["webhook-signature"] = "v2,abc"
        with pytest.raises(AuthenticationError):
            verify_signature(body, headers, [secret])

    def test_rotation_accepts_old_and_new_secret(self):
        old, new = _new_secret(), _new_secret()
        rule = _rule(
            secret=new,
            prev_secret=old,
            secret_rotated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        secrets = active_secrets(rule)
        body = b'{"event":"rotate"}'
        verify_signature(body, _headers(old, body), secrets)
        verify_signature(body, _headers(new, body), secrets)

    def test_rotation_old_secret_expires_after_24h(self):
        old, new = _new_secret(), _new_secret()
        rule = _rule(
            secret=new,
            prev_secret=old,
            secret_rotated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25),
        )
        assert active_secrets(rule) == [new]
        with pytest.raises(AuthenticationError):
            verify_signature(b"{}", _headers(old, b"{}"), active_secrets(rule))

    def test_original_bytes_not_reserialized(self):
        secret = _new_secret()
        body = b'{"a": 1,   "b": "x"}'
        headers = _headers(secret, body)
        verify_signature(body, headers, [secret])
        # 同一 JSON 重序列化（空格不同）后必须验签失败——证明用的是原始字节。
        with pytest.raises(AuthenticationError):
            verify_signature(b'{"a":1,"b":"x"}', headers, [secret])

    def test_header_lookup_is_case_insensitive(self):
        secret = _new_secret()
        body = b"{}"
        headers = {k.upper(): v for k, v in _headers(secret, body).items()}
        verify_signature(body, headers, [secret])

    def test_signature_uses_constant_time_compare(self):
        """常数时间比较无法从行为上观测（``==`` 与 compare_digest 同结果）。

        用源码守卫钉住 issue §3 的显式要求；变异试验「把 compare_digest 改成
        ==」会让本用例变红。
        """
        import re
        from pathlib import Path

        import app.services.webhook_service as service_module

        source = Path(service_module.__file__).read_text(encoding="utf-8")
        assert re.search(r"hmac\.compare_digest\(\s*expected\s*,\s*candidate\s*\)", source), (
            "签名比较必须使用 hmac.compare_digest（常数时间）；文档字符串提及不算"
        )


# ── 模板校验与渲染（注入防护）────────────────────────────────────────


class TestTemplate:
    def test_allowed_placeholders_render(self):
        payload = {"event": "ping", "n": 1}
        rule = SimpleNamespace(
            id="r1", prompt_template="payload={payload} rule={rule_id} d={delivery_id}"
        )
        rendered = render_prompt(rule, payload, "d1")
        assert PAYLOAD_OPEN in rendered and PAYLOAD_CLOSE in rendered
        assert '{"event": "ping", "n": 1}' in rendered
        assert "rule=r1" in rendered and "d=d1" in rendered

    def test_unknown_placeholder_rejected(self):
        with pytest.raises(ValidationError):
            validate_prompt_template("hello {foo}")

    @pytest.mark.parametrize(
        "template",
        [
            "you are a system_prompt",
            "use tools now",
            "set model to x",
            "temperature=0.1",
        ],
    )
    def test_forbidden_keywords_rejected(self, template):
        with pytest.raises(ValidationError):
            validate_prompt_template(template)

    def test_attribute_access_rejected(self):
        with pytest.raises(ValidationError):
            validate_prompt_template("{payload.__class__}")

    def test_conversion_and_format_spec_rejected(self):
        for template in ("{payload!r}", "{payload:>10}"):
            with pytest.raises(ValidationError):
                validate_prompt_template(template)

    def test_empty_template_rejected(self):
        with pytest.raises(ValidationError):
            validate_prompt_template("   ")

    def test_valid_template_accepted(self):
        validate_prompt_template("事件如下：\n{payload}\n来自 {rule_id}/{delivery_id}")


# ── 幂等键 ───────────────────────────────────────────────────────────


class TestIdempotencyKey:
    def test_webhook_id_preferred(self):
        assert derive_idempotency_key("msg_1", b"body") == "msg_1"

    def test_missing_webhook_id_uses_body_hash(self):
        body = b'{"a":1}'
        assert derive_idempotency_key(None, body) == hashlib.sha256(body).hexdigest()

    def test_overlong_webhook_id_falls_back_to_hash(self):
        body = b"x"
        assert derive_idempotency_key("m" * 129, body) == hashlib.sha256(body).hexdigest()


# ── body 上限 ────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, headers: dict[str, str], chunks: list[bytes] | None = None):
        self.headers = headers
        self._chunks = chunks or []
        self.stream_calls = 0

    async def stream(self):
        self.stream_calls += 1
        for chunk in self._chunks:
            yield chunk


class TestBodyLimit:
    @pytest.mark.asyncio
    async def test_content_length_over_limit_413_without_reading_stream(self):
        request = _FakeRequest({"content-length": "1000"}, [b"x" * 1000])
        with pytest.raises(PayloadTooLargeError) as exc_info:
            await read_body_with_limit(request, 10)
        assert exc_info.value.status_code == 413
        assert exc_info.value.error_code == "PAYLOAD_TOO_LARGE"
        assert exc_info.value.detail["max_body_bytes"] == 10
        assert request.stream_calls == 0, "Content-Length 超限时绝不能读 stream"

    @pytest.mark.asyncio
    async def test_chunked_body_accumulation_413(self):
        request = _FakeRequest({}, [b"12345", b"67890", b"X"])
        with pytest.raises(PayloadTooLargeError):
            await read_body_with_limit(request, 10)
        assert request.stream_calls == 1

    @pytest.mark.asyncio
    async def test_body_within_limit_read(self):
        request = _FakeRequest({"content-length": "6"}, [b"abc", b"def"])
        assert await read_body_with_limit(request, 10) == b"abcdef"

    @pytest.mark.asyncio
    async def test_invalid_content_length_falls_back_to_stream(self):
        request = _FakeRequest({"content-length": "not-a-number"}, [b"abc"])
        assert await read_body_with_limit(request, 10) == b"abc"


# ── 限流 ─────────────────────────────────────────────────────────────


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_limit_enforced_per_rule_and_ip(self):
        rule = SimpleNamespace(id=f"rl-{time.time_ns()}", rate_limit_per_minute=2)
        await enforce_rate_limit(rule, "1.2.3.4")
        await enforce_rate_limit(rule, "1.2.3.4")
        with pytest.raises(RateLimitError) as exc_info:
            await enforce_rate_limit(rule, "1.2.3.4")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["retry_after"] >= 1

    @pytest.mark.asyncio
    async def test_different_ip_has_separate_bucket(self):
        rule = SimpleNamespace(id=f"rl-{time.time_ns()}", rate_limit_per_minute=1)
        await enforce_rate_limit(rule, "1.1.1.1")
        await enforce_rate_limit(rule, "2.2.2.2")
