"""Webhook runtime service（issue #478）。

本模块集中实现：

- **secret 加密**：Fernet（``WEBHOOK_ENCRYPTION_KEY``，独立于 JWT SECRET_KEY）；
- **Standard Webhooks 验签**：HMAC-SHA256 over ``id.timestamp.body``、±300s、
  多签名/轮换期新旧 secret、``hmac.compare_digest``；
- **模板校验与渲染**：只允许 ``{payload}/{rule_id}/{delivery_id}``，payload 包在
  不可信分隔区里；
- **body 上限**：``Content-Length`` 预检（不读 stream）+ chunked 累计读取；
- **限流**：先于验签（issue #497）——所有请求（含坏签名）都消耗配额；未知
  rule_id 使用 ``unknown:{ip}`` 桶；Redis INCR 快路径，不可用时进程内计数兜底；
- **幂等**：``webhook-id`` 为 Standard Webhooks 必需头，直接作为幂等键
  （``≤128``；issue #497 删除了不可达的 body-sha256 兜底分支）；Redis SET NX EX
  （快路径，不可用降级）+ DB UNIQUE（最终仲裁）；
- **outbox**：``FOR UPDATE SKIP LOCKED`` 领取、10 分钟处理租约、3 次有界重试；
  **租约 fencing**（issue #497）：领取时轮换 ``processing_token`` 并
  ``lease_version += 1``，完成/失败写回都带 token + status 条件更新，rowcount 0
  即视为租约已被接管，不再写会话/消息（超时重领绝不双写）；
- **reuse 历史**：``session_mode=reuse`` 命中会话后加载最近
  ``WEBHOOK_REUSE_HISTORY_LIMIT`` 条消息，按 agent 聊天格式传入
  ``AgentService.chat(history=...)``；``session_key`` 缺失回退 new，并在投递
  记录 ``error`` 摘要标注 ``reuse_fallback=true``；
- **会话落库**：直接调用 ``AgentService.chat``（不走 HTTP），再经
  ``save_session_interaction`` 写消息。

与 issue §5.1 的偏离：``webhook_deliveries`` 增加 ``payload`` 列（issue 清单
只有 payload_hash）。异步 outbox 在请求结束之后才处理投递，没有持久化 payload
就无法渲染 prompt——这是让 issue 的 outbox 语义可实现的必要补充，见 ADR-005。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from string import Formatter
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import FinalAnswerAccumulator
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.log_safety import log_safe
from app.core.prometheus import (
    WEBHOOK_DELIVERY_TOTAL,
    WEBHOOK_IDEMPOTENCY_REDIS_FALLBACK_TOTAL,
    WEBHOOK_POLL_ERRORS_TOTAL,
    WEBHOOK_RATE_LIMITED_TOTAL,
)
from app.core.redis import redis_client
from app.exceptions import (
    AuthenticationError,
    PayloadTooLargeError,
    RateLimitError,
    ValidationError,
)
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookRule,
    WebhookSessionMode,
)
from app.services.audit_service import audit_service
from app.services.session_service import save_session_interaction

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────

SECRET_SCHEME_PREFIX = "whsec_"
IDEMPOTENCY_TTL_SECONDS = 604800  # 7 天（issue §5.2）
IDEMPOTENCY_KEY_PREFIX = "wh:idem:"
RATE_LIMIT_KEY_PREFIX = "wh:rl:"
PROCESSING_LEASE = timedelta(minutes=10)
POLL_BATCH_SIZE = 10
RETRY_BASE_SECONDS = 30
SECRET_ROTATION_GRACE = timedelta(hours=24)
# issue #497 §2：reuse 缺少有效 session_key 回退 new 时写入投递 error 摘要的前缀
# （不新增列；成功写回时保留该前缀，见 ``_complete_delivery``）。
REUSE_FALLBACK_MARKER = "reuse_fallback=true"

PAYLOAD_OPEN = "<<<UNTRUSTED_WEBHOOK_PAYLOAD>>>"
PAYLOAD_CLOSE = "<<<END_UNTRUSTED_WEBHOOK_PAYLOAD>>>"

_ALLOWED_PLACEHOLDERS = frozenset({"payload", "rule_id", "delivery_id"})
# 规则模板不得出现系统提示词/工具/模型参数关键字（issue §5.5）。用词边界匹配，
# 避免误伤 "models"/"modeling" 这类普通词。
_FORBIDDEN_TEMPLATE_RE = re.compile(
    r"\b(system_prompt|system|tools|tool_choice|model|messages|temperature|max_tokens|role)\b",
    re.IGNORECASE,
)
# 攻击者可在 JSON 字符串里塞入分隔符本身，伪造"分隔区已结束"。渲染时把
# 命中分隔符形态的尖括号替换成视觉相似字符，确保分隔区只有一个真实出口。
_PAYLOAD_MARKER_RE = re.compile(r"<<<\s*(?:END_)?UNTRUSTED_WEBHOOK_PAYLOAD\s*>>>", re.IGNORECASE)

# 进程内限流兜底（Redis 不可用时；多 worker 下为近似值，见 ADR 已知风险）。
_memory_rate: dict[str, tuple[int, int]] = {}


class WebhookEncryptionError(RuntimeError):
    """WEBHOOK_ENCRYPTION_KEY 缺失/非法，或密文无法解密（显式失败，绝不吞）。"""


class WebhookDeliveryError(RuntimeError):
    """投递处理失败（消息已脱敏，可安全写入 *_deliveries.error*）。"""


class WebhookDeliveryTerminalError(WebhookDeliveryError):
    """取消/暂停终态：不得成功投递，也不得再次自动重试。"""

    retry = False


# ── 时间 ─────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """naive UTC —— 与仓库 DateTime 列（MySQL DATETIME / SQLite）口径一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


# ── secret 加密（Fernet）─────────────────────────────────────────────


def _fernet() -> Fernet:
    key = (settings.WEBHOOK_ENCRYPTION_KEY or "").strip()
    if not key:
        raise WebhookEncryptionError(
            "WEBHOOK_ENCRYPTION_KEY 未配置（Webhook secret 无法加解密；"
            "该密钥必须独立于 JWT SECRET_KEY）"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WebhookEncryptionError(f"WEBHOOK_ENCRYPTION_KEY 非法: {exc}") from exc


def generate_secret() -> str:
    """``whsec_<base64(32 random bytes)>``（issue §5.1）。"""
    return SECRET_SCHEME_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise WebhookEncryptionError(
            "webhook secret 解密失败（WEBHOOK_ENCRYPTION_KEY 与密文不匹配或密文损坏）"
        ) from exc


def secret_prefix(plain: str) -> str:
    """响应里只暴露 secret 的前 6 位（issue §5.6）。

    注意：所有 secret 都以 ``whsec_`` 为方案前缀，因此这个前缀按设计不泄露
    任何随机材料（恰好 6 位）；完整明文只在创建/轮换响应中出现一次。
    """
    return plain[:6]


def active_secrets(rule: WebhookRule) -> list[str]:
    """验签接受的 secret 列表：当前 + 轮换后 24h 内的旧 secret。"""
    values = [decrypt_secret(rule.secret_encrypted)]
    if rule.secret_prev_encrypted and rule.secret_rotated_at is not None:
        if _utcnow() - rule.secret_rotated_at <= SECRET_ROTATION_GRACE:
            values.append(decrypt_secret(rule.secret_prev_encrypted))
    return values


def validate_webhook_settings() -> None:
    """启动校验（fail-closed）：启用 webhook 时必须有合法 Fernet key。"""
    if not settings.WEBHOOKS_ENABLED:
        return
    _fernet()


# ── Standard Webhooks 验签 ───────────────────────────────────────────


def _normalized_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _decode_signature_secret(secret: str) -> bytes:
    raw = secret[len(SECRET_SCHEME_PREFIX) :] if secret.startswith(SECRET_SCHEME_PREFIX) else secret
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AuthenticationError("webhook secret 格式非法") from exc


def verify_signature(
    raw_body: bytes,
    headers: Mapping[str, str],
    secrets: list[str],
    tolerance: int | None = None,
) -> None:
    """Standard Webhooks 验签；失败一律 ``AuthenticationError``（401）。

    - 签名内容：``f"{webhook-id}.{webhook-timestamp}." + raw_body``（原始字节，
      绝不重序列化）；
    - ``webhook-signature`` 允许空格分隔多个 ``v1,<base64>``，任一匹配即过；
    - 时间戳偏差（过去或未来）超过 tolerance → 401；
    - 逐候选 ``hmac.compare_digest``（常数时间）。
    """
    normalized = _normalized_headers(headers)
    message_id = normalized.get("webhook-id")
    timestamp = normalized.get("webhook-timestamp")
    signature_header = normalized.get("webhook-signature")
    if not message_id or not timestamp or not signature_header:
        raise AuthenticationError("缺少 webhook 签名头")

    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("webhook-timestamp 非法") from exc

    window = (
        int(settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS) if tolerance is None else int(tolerance)
    )
    now = int(time.time())
    if abs(now - timestamp_value) > window:
        raise AuthenticationError("webhook 时间戳超出允许窗口（疑似重放）")

    signed = f"{message_id}.{timestamp}.".encode() + raw_body
    candidates: list[str] = []
    for part in signature_header.split():
        version, sep, signature = part.partition(",")
        if sep and version == "v1" and signature:
            candidates.append(signature.strip())
    if not candidates:
        raise AuthenticationError("webhook-signature 中没有 v1 签名")

    for secret in secrets:
        try:
            key = _decode_signature_secret(secret)
        except AuthenticationError:
            # 单个损坏的 secret 不应让其他（轮换期）secret 的匹配机会消失。
            continue
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
        for candidate in candidates:
            if hmac.compare_digest(expected, candidate):
                return
    raise AuthenticationError("webhook 签名校验失败")


# ── body 上限 ────────────────────────────────────────────────────────


def effective_max_body_bytes(rule: WebhookRule) -> int:
    """全局上限是硬顶：规则只能收紧，不能放大（防配置错误放宽全局边界）。"""
    global_limit = max(1, int(settings.WEBHOOK_MAX_BODY_BYTES))
    rule_limit = int(rule.max_body_bytes or global_limit)
    return min(max(1, rule_limit), global_limit)


def _declared_content_length(request: Any) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def read_body_with_limit(request: Any, limit: int) -> bytes:
    """先看 Content-Length（超限直接 413，**不读 stream**），再受控累计读取。

    chunked 请求没有 Content-Length，必须流式读取并逐块检查累计值——不能信
    「头部声称的大小」。超限时立即停止读取（不再消费剩余 body）。
    """
    declared = _declared_content_length(request)
    if declared is not None and declared > limit:
        raise PayloadTooLargeError(detail={"max_body_bytes": limit, "received_bytes": declared})

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLargeError(detail={"max_body_bytes": limit, "received_bytes": total})
        chunks.append(chunk)
    return b"".join(chunks)


# ── 模板校验与渲染（注入防护）────────────────────────────────────────


def validate_prompt_template(template: str) -> None:
    """保存规则时校验模板：仅允许 3 个裸占位符 + 拒绝保留关键字。"""
    if not isinstance(template, str) or not template.strip():
        raise ValidationError("prompt_template 不能为空", field="prompt_template")
    if _FORBIDDEN_TEMPLATE_RE.search(template):
        raise ValidationError(
            "prompt_template 不得包含系统提示词/工具/模型等保留关键字",
            field="prompt_template",
        )
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValidationError(f"prompt_template 格式非法: {exc}", field="prompt_template") from exc
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in _ALLOWED_PLACEHOLDERS:
            raise ValidationError(
                "prompt_template 仅允许 {payload}/{rule_id}/{delivery_id} 占位符",
                field="prompt_template",
            )
        if format_spec or conversion:
            # {payload.__class__} / {payload!r} / {payload:>10} 等属性访问与转换一律拒绝。
            raise ValidationError(
                "prompt_template 占位符不支持属性访问/转换/格式说明",
                field="prompt_template",
            )


def wrap_untrusted_payload(payload: Any) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    # 防"分隔符逃逸"：payload 里出现 <<<END_UNTRUSTED...>>> 时，把尖括号换成
    # 单书名号（‹›），语义仍可读但不再构成真实分隔符。
    body = _PAYLOAD_MARKER_RE.sub(
        lambda match: match.group(0).replace("<", "‹").replace(">", "›"), body
    )
    return f"{PAYLOAD_OPEN}\n{body}\n{PAYLOAD_CLOSE}"


def render_prompt(rule: WebhookRule, payload: Any, delivery_id: str) -> str:
    return rule.prompt_template.format_map(
        {
            "payload": wrap_untrusted_payload(payload),
            "rule_id": rule.id,
            "delivery_id": delivery_id,
        }
    )


# ── 幂等 ────────────────────────────────────────────────────────────


def derive_idempotency_key(webhook_id: str | None) -> str:
    """``webhook-id`` 即幂等键；Standard Webhooks 必需头，缺失/超长显式拒绝。

    issue #497：调用方（端点）在验签之后调用，缺失 id 已在验签阶段 401；
    这里仍防御性校验（直调/未来复用）。旧实现「缺失或超 128 位时退化为
    body sha256」的分支自 ``verify_signature`` 强制 ``webhook-id`` 起即不可达，
    已删除；超过列宽 128 直接 400，绝不再静默换成 body 哈希（那会让
    同一事件在 id 超长时退化为按 body 去重，语义漂移）。
    """
    if not webhook_id:
        raise ValidationError("缺少 webhook-id（Standard Webhooks 必需头）", field="webhook-id")
    if len(webhook_id) > 128:
        raise ValidationError("webhook-id 长度不得超过 128", field="webhook-id")
    return webhook_id


async def mark_idempotency_in_redis(rule_id: str, idempotency_key: str) -> bool | None:
    """Redis SET NX EX 快路径。

    返回 True（首次）/ False（已见过）/ None（Redis 不可用，降级 DB）。返回值
    只用于日志与指标：**DB 唯一约束才是最终仲裁**——即使 Redis 说"首次"，
    插入仍可能撞唯一约束（多 worker）；Redis 说"已见"但 DB 无行（上次在
    Redis 写入后、DB 提交前崩溃）时也必须继续插入，绝不能静默丢事件。
    """
    key = f"{IDEMPOTENCY_KEY_PREFIX}{rule_id}:{idempotency_key}"
    try:
        if not redis_client:
            raise RuntimeError("Redis 未初始化")
        created = await redis_client.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL_SECONDS)
        return bool(created)
    except Exception as exc:
        WEBHOOK_IDEMPOTENCY_REDIS_FALLBACK_TOTAL.inc()
        logger.warning(
            "webhook 幂等 Redis 不可用，降级为 DB 唯一约束（rule=%s）: %s",
            log_safe(rule_id),
            log_safe(exc),
        )
        return None


# ── 限流（规则 + IP；先于验签）───────────────────────────────────────


async def enforce_rate_limit(rule: WebhookRule | None, client_ip: str) -> None:
    """每分钟固定窗口限流；超限 ``RateLimitError``（429，超限不落投递行）。

    issue #497：限流前置于验签——所有请求（含坏签名/未知规则）都消耗配额。
    ``rule`` 为 None（未知或禁用规则）时使用 ``unknown:{ip}`` 桶与全局默认
    限额，既防枚举也防「随机 rule_id 无限打」。Redis 不可用时沿用既有进程内
    计数降级（多 worker 下为近似值，见 ADR 已知风险）。
    """
    if rule is None:
        scope = "unknown"
        limit = int(settings.WEBHOOK_RATE_LIMIT_PER_MINUTE)
    else:
        scope = str(rule.id)
        limit = int(rule.rate_limit_per_minute or settings.WEBHOOK_RATE_LIMIT_PER_MINUTE)
    limit = max(1, limit)
    now = int(time.time())
    window = now // 60
    key = f"{RATE_LIMIT_KEY_PREFIX}{scope}:{client_ip}:{window}"

    count: int | None = None
    try:
        if redis_client:
            count = int(await redis_client.incr(key))
            if count == 1:
                await redis_client.expire(key, 130)
    except Exception as exc:
        logger.warning("webhook 限流 Redis 不可用，降级为进程内计数: %s", log_safe(exc))
        count = None

    if count is None:
        current = _memory_rate.get(key)
        if current is not None and current[0] == window:
            count = current[1] + 1
        else:
            count = 1
        _memory_rate[key] = (window, count)
        if len(_memory_rate) > 4096:  # 防无界增长（多规则/多 IP 的过期窗口）
            _memory_rate.clear()

    if count > limit:
        WEBHOOK_RATE_LIMITED_TOTAL.inc()
        retry_after = max(1, 60 - (now % 60))
        raise RateLimitError(
            detail={"max_per_minute": limit, "retry_after": retry_after},
        )


# ── 投递登记（端点 → DB outbox）──────────────────────────────────────


@dataclass(frozen=True)
class DeliveryResult:
    """端点幂等状态机的输出。"""

    delivery_id: str
    status: str
    duplicate: bool
    http_status: int

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"delivery_id": self.delivery_id, "status": self.status}
        if self.duplicate:
            data["duplicate"] = True
        return data


def _existing_outcome(db_session: AsyncSession, delivery: WebhookDelivery) -> DeliveryResult:
    """已存在投递行的幂等语义（issue §5.2）；终态 failed 在这里重置。"""
    status = delivery.status
    if status == WebhookDeliveryStatus.SUCCEEDED:
        return DeliveryResult(delivery.id, "succeeded", True, 200)
    if status == WebhookDeliveryStatus.FAILED and delivery.next_attempt_at is None:
        # 三次失败后的终态：发送方同一幂等键重投 = 明确要求重新入队。
        delivery.status = WebhookDeliveryStatus.RECEIVED
        delivery.attempts = 0
        delivery.next_attempt_at = None
        delivery.processing_started_at = None
        delivery.processed_at = None
        delivery.error = None
        return DeliveryResult(delivery.id, "received", True, 202)
    # received / processing / 仍会自动重试的 failed
    return DeliveryResult(delivery.id, status.value, True, 202)


async def register_delivery(
    db_session: AsyncSession,
    rule: WebhookRule,
    *,
    idempotency_key: str,
    payload_text: str,
    payload_hash: str,
) -> DeliveryResult:
    """幂等登记：返回已有行语义或新建 ``received`` 行并提交。"""
    stmt = select(WebhookDelivery).where(
        WebhookDelivery.rule_id == rule.id,
        WebhookDelivery.idempotency_key == idempotency_key,
    )
    existing = (await db_session.execute(stmt)).scalars().first()
    if existing is not None:
        result = _existing_outcome(db_session, existing)
        await db_session.commit()
        return result

    delivery = WebhookDelivery(
        id=str(uuid.uuid4()),
        rule_id=rule.id,
        org_id=rule.org_id,
        idempotency_key=idempotency_key,
        status=WebhookDeliveryStatus.RECEIVED,
        payload_hash=payload_hash,
        payload=payload_text,
        attempts=0,
    )
    db_session.add(delivery)
    try:
        await db_session.commit()
    except IntegrityError:
        # 并发同 id：唯一约束兜底，回滚后按"已存在"语义返回。
        await db_session.rollback()
        existing = (await db_session.execute(stmt)).scalars().first()
        if existing is None:  # pragma: no cover - 唯一约束错误必伴随一行存在
            raise
        result = _existing_outcome(db_session, existing)
        await db_session.commit()
        return result
    return DeliveryResult(delivery.id, "received", False, 202)


# ── outbox 领取与处理 ────────────────────────────────────────────────


def build_claim_stmt(now: datetime):
    """poll 领取语句：received / 到点 failed / 租约超时 processing，SKIP LOCKED。

    独立成函数是为了让单测能对 MySQL 方言编译并断言 ``FOR UPDATE SKIP LOCKED``
    确实在 SQL 里（SQLite 方言会忽略该子句，运行时无法验证）。
    """
    stale_before = now - PROCESSING_LEASE
    return (
        select(WebhookDelivery)
        .where(
            or_(
                WebhookDelivery.status == WebhookDeliveryStatus.RECEIVED,
                and_(
                    WebhookDelivery.status == WebhookDeliveryStatus.FAILED,
                    WebhookDelivery.next_attempt_at.is_not(None),
                    WebhookDelivery.next_attempt_at <= now,
                ),
                and_(
                    WebhookDelivery.status == WebhookDeliveryStatus.PROCESSING,
                    WebhookDelivery.processing_started_at.is_not(None),
                    WebhookDelivery.processing_started_at <= stale_before,
                ),
            )
        )
        .order_by(WebhookDelivery.received_at)
        .limit(POLL_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )


class WebhookService:
    """outbox poller + 投递处理（无状态；所有可变量都在 DB/Redis）。"""

    # ── 处理单个投递 ────────────────────────────────────────────────

    def _default_agent_service(self):
        from app.agent.service import AgentService

        return AgentService()

    async def _run_chat(
        self,
        agent: Any,
        rule: WebhookRule,
        session_id: str,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        answers: FinalAnswerAccumulator | None = None,
    ) -> str:
        """直接调用 ``AgentService.chat``（禁止 HTTP 自调用），使用权威答案。"""
        answers = answers or FinalAnswerAccumulator()
        async for event in agent.chat(
            query=prompt,
            history=list(history or []),
            organization_id=rule.org_id,
            user_id=rule.created_by,
            session_id=session_id,
            config=None,
        ):
            event_type = getattr(event, "type", None)
            answers.add(event)
            if event_type == "error":
                raise WebhookDeliveryError(
                    f"agent 返回 error 事件: {getattr(event, 'content', '')}"
                )
        if answers.task_state in (
            "cancelled",
            "paused",
            "waiting_approval",
            "waiting_plan_approval",
        ):
            raise WebhookDeliveryTerminalError("agent 已取消或暂停，停止本次投递且不重试")
        if answers.blocked or answers.task_state == "failed":
            raise WebhookDeliveryError("agent 执行失败，不投递 assistant 正文")
        return answers.answer

    async def _load_reuse_history(
        self,
        db_session: AsyncSession,
        session_id: str,
    ) -> list[dict[str, str]]:
        """加载会话最近 N 条消息，按 agent 聊天格式（role/content）返回。

        issue #497 §2：reuse 模式必须真正延续上下文，而不是只复用行。取最近
        ``WEBHOOK_REUSE_HISTORY_LIMIT`` 条（seq 倒序取再反转为时间正序），
        与 ``/chat`` 的 history 口径一致（user/assistant 两角色）。
        """
        limit = max(1, int(settings.WEBHOOK_REUSE_HISTORY_LIMIT))
        rows = (
            (
                await db_session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.seq.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "role": "user" if message.message_type == MessageType.USER else "assistant",
                "content": message.content,
            }
            for message in reversed(rows)
        ]

    async def _ensure_session(
        self,
        db_session: AsyncSession,
        delivery: WebhookDelivery,
        rule: WebhookRule,
        payload: Any,
        processing_token: str,
    ) -> str:
        """解析投递对应的 ChatSession；重试复用 delivery.session_id。

        issue #497 §4：会话/session_id 的写回同样带租约条件（txn 内条件 UPDATE，
        rowcount 0 即租约已被接管 → rollback + 放弃），避免旧 worker 在超时
        重领后把 succeeded 行/新租约的 session_id 覆盖成自己的空会话。
        """
        if delivery.session_id:
            existing = await db_session.get(ChatSession, delivery.session_id)
            if existing is not None:
                return existing.id

        session_key: str | None = None
        if rule.session_mode == WebhookSessionMode.REUSE:
            raw_key = payload.get("session_key") if isinstance(payload, dict) else None
            if isinstance(raw_key, str) and 0 < len(raw_key) <= 128:
                session_key = raw_key
                found = (
                    (
                        await db_session.execute(
                            select(ChatSession).where(
                                ChatSession.webhook_rule_id == rule.id,
                                ChatSession.webhook_session_key == session_key,
                                ChatSession.user_id == rule.created_by,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if found is not None:
                    await self._assign_session_id(
                        db_session, delivery.id, processing_token, found.id
                    )
                    return found.id
            else:
                # issue #497 §2：session_key 缺失/非法 → 回退 new，并在投递记录
                # 标注 reuse_fallback=true（不新增列，写入 error 摘要；成功写回时
                # 保留该前缀，见 _complete_delivery）。
                delivery.error = (
                    f"{REUSE_FALLBACK_MARKER}（payload 缺少有效 session_key，按 new 新建会话）"
                )

        session_id = str(uuid.uuid4())
        session = ChatSession(
            id=session_id,
            user_id=rule.created_by,
            organization_id=rule.org_id,
            title=f"[webhook] {rule.name}"[:80],
            status=ChatSessionStatus.ACTIVE,
            root_session_id=session_id,
            source="webhook",
            webhook_rule_id=rule.id,
            webhook_delivery_id=delivery.id,
            webhook_session_key=session_key,
        )
        db_session.add(session)
        # 与新建会话同一事务：条件 UPDATE 把 session_id 写回投递行；租约已易主
        # 时 rollback（新建会话一并撤销），绝不产生"孤儿会话指针"。
        await self._assign_session_id(db_session, delivery.id, processing_token, session_id)
        return session_id

    async def _assign_session_id(
        self,
        db_session: AsyncSession,
        delivery_id: str,
        processing_token: str,
        session_id: str,
    ) -> None:
        """token fenced 地把 delivery.session_id 指向会话并提交事务。"""
        result = await db_session.execute(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.id == delivery_id,
                WebhookDelivery.processing_token == processing_token,
                WebhookDelivery.status == WebhookDeliveryStatus.PROCESSING,
            )
            .values(session_id=session_id)
        )
        if result.rowcount == 0:
            await db_session.rollback()
            raise WebhookDeliveryError("租约已被接管（会话写回 fence），放弃本次处理")
        await db_session.commit()

    async def _mark_failed(
        self,
        session_factory: Any,
        delivery_id: str,
        processing_token: str,
        error: str,
        *,
        expected_status: WebhookDeliveryStatus = WebhookDeliveryStatus.PROCESSING,
        retry: bool = True,
    ) -> bool:
        """失败写回（token fenced）：只有仍持有租约的 worker 才能改状态/排重试。

        issue #497 §4：超时重领后 token 已轮换，旧 worker 的失败写回必须落空
        （rowcount 0），否则会把新 worker 正在 processing 的行改成 failed。
        ``expected_status`` 供「完成写回后消息落库失败」的补偿（SUCCEEDED→FAILED）。

        Returns: 是否真正写回（False = 状态不符或租约已易主，调用方不应据此
        记录失败审计，避免旧 worker 的噪声覆盖新 worker 的真实结果）。
        """
        async with session_factory() as db_session:
            delivery = await db_session.get(WebhookDelivery, delivery_id)
            if delivery is None or delivery.status != expected_status:
                return False
            if delivery.processing_token != processing_token:
                logger.warning(
                    "webhook 投递租约已被接管（fence），跳过失败写回（delivery=%s）",
                    log_safe(delivery_id),
                )
                return False
            now = _utcnow()
            max_attempts = max(1, int(settings.WEBHOOK_MAX_ATTEMPTS))
            next_attempt_at = None
            if retry and delivery.attempts < max_attempts:
                # 2^attempts * 30s（attempts 在领取时已 +1）：首次失败 60s 后重试。
                next_attempt_at = now + timedelta(
                    seconds=(2 ** max(1, delivery.attempts)) * RETRY_BASE_SECONDS
                )
            # 终态（next_attempt_at=None）：轮询不再领取；发送方同一幂等键重投
            # 可重置（register_delivery）。
            result = await db_session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.processing_token == processing_token,
                    WebhookDelivery.status == expected_status,
                )
                .values(
                    status=WebhookDeliveryStatus.FAILED,
                    error=log_safe(error, max_len=500),
                    processed_at=None,
                    next_attempt_at=next_attempt_at,
                )
            )
            await db_session.commit()
            if result.rowcount:
                WEBHOOK_DELIVERY_TOTAL.labels(status="failed").inc()
            return result.rowcount > 0

    async def _complete_delivery(
        self,
        session_factory: Any,
        delivery_id: str,
        processing_token: str,
        session_id: str,
    ) -> bool:
        """完成写回（token fencing）；返回 False ⇒ 租约已被重领。

        issue #497 §4 规定的条件更新：
        ``UPDATE webhook_deliveries SET status='succeeded', processed_at=NOW()
        WHERE id=:id AND processing_token=:token AND status='processing'``。
        rowcount 为 0 时调用方**不得**再写会话/消息（迟到 worker 的结果丢弃）。
        成功时保留 ``reuse_fallback=true`` 前缀（回退标注不被成功写回抹掉）。
        """
        preserve_fallback = case(
            (WebhookDelivery.error.startswith(REUSE_FALLBACK_MARKER), WebhookDelivery.error),
            else_=None,
        )
        async with session_factory() as db_session:
            result = await db_session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.processing_token == processing_token,
                    WebhookDelivery.status == WebhookDeliveryStatus.PROCESSING,
                )
                .values(
                    status=WebhookDeliveryStatus.SUCCEEDED,
                    session_id=session_id,
                    processed_at=_utcnow(),
                    next_attempt_at=None,
                    error=preserve_fallback,
                )
            )
            await db_session.commit()
            return result.rowcount > 0

    async def process_delivery(
        self,
        delivery_id: str,
        *,
        processing_token: str,
        session_factory: Any | None = None,
        agent_service: Any | None = None,
    ) -> None:
        """处理一个已被领取（status=processing 且 token 匹配）的投递。"""
        factory = session_factory or AsyncSessionLocal
        agent = agent_service or self._default_agent_service()

        async with factory() as db_session:
            delivery = await db_session.get(WebhookDelivery, delivery_id)
            if delivery is None or delivery.status != WebhookDeliveryStatus.PROCESSING:
                return
            if delivery.processing_token != processing_token:
                # 入口 fence：领取后处理前已被他 worker 重领（token 轮换）。
                logger.warning(
                    "webhook 投递租约已被接管（入口 fence），跳过处理（delivery=%s）",
                    log_safe(delivery_id),
                )
                return
            rule = await db_session.get(WebhookRule, delivery.rule_id)
            if rule is None:
                await self._mark_failed(factory, delivery_id, processing_token, "规则已删除")
                return
            try:
                payload = json.loads(delivery.payload)
            except (TypeError, ValueError) as exc:
                await self._mark_failed(
                    factory, delivery_id, processing_token, f"payload JSON 解析失败: {exc}"
                )
                return
            try:
                session_id = await self._ensure_session(
                    db_session, delivery, rule, payload, processing_token
                )
            except Exception as exc:
                await self._mark_failed(
                    factory, delivery_id, processing_token, f"会话创建失败: {type(exc).__name__}"
                )
                return
            # issue #497 §2：reuse 模式加载会话历史（本次投递的消息在完成后才写）。
            history = (
                await self._load_reuse_history(db_session, session_id)
                if rule.session_mode == WebhookSessionMode.REUSE
                else []
            )

        try:
            prompt = render_prompt(rule, payload, delivery_id)
            answers = FinalAnswerAccumulator()
            answer = await self._run_chat(agent, rule, session_id, prompt, history, answers=answers)
        except Exception as exc:
            logger.warning(
                "webhook 投递处理失败（delivery=%s rule=%s）: %s",
                log_safe(delivery_id),
                log_safe(rule.id),
                log_safe(exc),
            )
            if await self._mark_failed(
                factory,
                delivery_id,
                processing_token,
                f"{type(exc).__name__}: {exc}",
                retry=getattr(exc, "retry", True),
            ):
                # 只有仍持有租约的失败才写审计；被 fence 掉的旧 worker 不污染。
                await audit_service.log_activity(
                    user_id=rule.created_by,
                    action="process_webhook_delivery",
                    target_type="webhook_delivery",
                    target_id=delivery_id,
                    detail=json.dumps({"status": "failed", "error": log_safe(exc, max_len=200)}),
                )
            return

        # issue #497 §4：完成写回带 token 条件（fencing）。rowcount 0 ⇒ 租约已被
        # 重领，迟到结果直接丢弃，绝不写会话/消息（防双写）。
        if not await self._complete_delivery(factory, delivery_id, processing_token, session_id):
            logger.warning(
                "webhook 投递租约已被接管（完成 fence），丢弃迟到结果（delivery=%s）",
                log_safe(delivery_id),
            )
            return

        try:
            await save_session_interaction(
                session_id,
                user_id=rule.created_by,
                query=prompt,
                organization_id=rule.org_id,
                assistant_response=answer,
                session_factory=factory,
                agent_task_id=answers.task_id or None,
            )
        except Exception as exc:
            # 行已标记 succeeded 后消息落库失败：补偿回可重试 failed，避免
            # 「状态已成功但消息永久缺失」。补偿同样带 token 条件。
            logger.error(
                "webhook 投递消息落库失败（delivery=%s session=%s），回退为可重试失败: %s",
                log_safe(delivery_id),
                log_safe(session_id),
                log_safe(exc),
            )
            await self._mark_failed(
                factory,
                delivery_id,
                processing_token,
                f"消息落库失败: {type(exc).__name__}",
                expected_status=WebhookDeliveryStatus.SUCCEEDED,
            )
            return

        WEBHOOK_DELIVERY_TOTAL.labels(status="succeeded").inc()
        await audit_service.log_activity(
            user_id=rule.created_by,
            action="process_webhook_delivery",
            target_type="webhook_delivery",
            target_id=delivery_id,
            detail=json.dumps({"status": "succeeded", "session_id": session_id}),
        )

    # ── poll 循环 ───────────────────────────────────────────────────

    async def poll_once(
        self,
        *,
        session_factory: Any | None = None,
        agent_service: Any | None = None,
    ) -> int:
        """领取一批（≤10）投递，轮换租约 token 并提交，然后逐个处理。"""
        factory = session_factory or AsyncSessionLocal
        now = _utcnow()
        claimed: list[tuple[str, str]] = []
        async with factory() as db_session:
            rows = (await db_session.execute(build_claim_stmt(now))).scalars().all()
            for row in rows:
                row.status = WebhookDeliveryStatus.PROCESSING
                row.processing_started_at = now
                row.attempts = int(row.attempts or 0) + 1
                # issue #497 §4：每次领取（含超时重领 / failed 到点重试）都轮换
                # token 并递增租约代数；旧 worker 的写回由 token 条件自然失效。
                row.lease_version = int(row.lease_version or 0) + 1
                row.processing_token = str(uuid.uuid4())
                claimed.append((row.id, row.processing_token))
            if claimed:
                # 先提交领取结果再处理，避免整个 LLM 调用期间持有行锁/长事务。
                await db_session.commit()

        for delivery_id, token in claimed:
            await self.process_delivery(
                delivery_id,
                processing_token=token,
                session_factory=factory,
                agent_service=agent_service,
            )
        return len(claimed)

    async def poll_loop(self, *, session_factory: Any | None = None) -> None:
        """lifespan 启动的后台循环；单次失败隔离，不终止循环。"""
        while True:
            try:
                await self.poll_once(session_factory=session_factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                WEBHOOK_POLL_ERRORS_TOTAL.inc()
                logger.error("webhook outbox poll 失败（继续下一轮）: %s", log_safe(exc))
            await asyncio.sleep(max(1, int(settings.WEBHOOK_WORKER_POLL_SECONDS)))


webhook_service = WebhookService()
