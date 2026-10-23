"""Webhook runtime HTTP 端点（issue #478）。

两个面：

- **入站** ``POST /api/v1/webhooks/{rule_id}``（无 JWT）：原始 body → 上限 →
  验签 → 解析 → 限流 → 幂等登记 → 202 快速返回（异步由 outbox poller 处理）。
  签名错误 401、规则不存在/禁用 404、body 超限 413、限流 429 + ``Retry-After``。
- **管理**（全部组织管理员 ``require_org_admin``）：规则 CRUD、secret 轮换、
  dry-run（只渲染/验签，不写投递、不建会话）、投递列表。

组织隔离：规则查询带 ``org_id``；跨组织规则一律 404（不泄露存在性）；
同组织非管理员 403。

响应包裹沿用仓库惯例 ``{"success": True, "data": ...}``；错误走全局
``AppError`` handler（429 例外：为携带 ``Retry-After`` 头，端点内直接构造）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    coerce_org_id,
    get_current_user,
    get_user_org_id,
    is_platform_admin,
    require_org_admin,
)
from app.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.models.organization import user_organization
from app.models.user import User
from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookProvider,
    WebhookRule,
    WebhookSessionMode,
)
from app.services.audit_service import audit_service, extract_client_ip
from app.services.webhook_service import (
    _utcnow,
    active_secrets,
    decrypt_secret,
    derive_idempotency_key,
    effective_max_body_bytes,
    encrypt_secret,
    enforce_rate_limit,
    generate_secret,
    mark_idempotency_in_redis,
    read_body_with_limit,
    register_delivery,
    render_prompt,
    secret_prefix,
    validate_prompt_template,
    verify_signature,
)

router = APIRouter()

_STATUS_BY_VALUE = {status.value: status for status in WebhookDeliveryStatus}


# ── 请求/响应模型（本 issue 不改 app/schemas/，模型就近定义）─────────


class RuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    prompt_template: str
    provider: str | None = None
    session_mode: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)
    max_body_bytes: int | None = Field(default=None, ge=1)
    enabled: bool = False
    organization_id: int | None = None


class RuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    prompt_template: str | None = None
    enabled: bool | None = None
    session_mode: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)
    max_body_bytes: int | None = Field(default=None, ge=1)


class DryRunRequest(BaseModel):
    payload: Any = Field(default_factory=dict)
    signature_headers: dict[str, str] | None = None


# ── 组织/权限辅助 ────────────────────────────────────────────────────


def _resolve_org(current_user: User, organization_id: int | None) -> int:
    target = (
        coerce_org_id(organization_id)
        if organization_id is not None
        else coerce_org_id(get_user_org_id(current_user))
    )
    if target is None:
        raise AuthorizationError("无法确定组织上下文")
    return target


async def _is_org_member(db: AsyncSession, current_user: User, org_id: int) -> bool:
    if coerce_org_id(get_user_org_id(current_user)) == org_id:
        return True
    membership = await db.execute(
        select(user_organization.c.user_id).where(
            user_organization.c.user_id == current_user.id,
            user_organization.c.organization_id == org_id,
        )
    )
    return membership.scalar_one_or_none() is not None


async def _load_rule_for_admin(db: AsyncSession, current_user: User, rule_id: str) -> WebhookRule:
    """按 id 加载规则并做管理权限校验；跨组织一律 404（不泄露存在性）。"""
    rule = await db.get(WebhookRule, rule_id)
    if rule is None:
        raise NotFoundError("Webhook 规则不存在")
    if is_platform_admin(current_user):
        return rule
    if not await _is_org_member(db, current_user, rule.org_id):
        raise NotFoundError("Webhook 规则不存在")
    await require_org_admin(db, current_user, rule.org_id)
    return rule


def _rule_data(rule: WebhookRule, *, plaintext_secret: str | None = None) -> dict[str, Any]:
    """规则序列化：永不回传密文；secret_prefix 为前 6 位。"""
    data: dict[str, Any] = {
        "id": rule.id,
        "org_id": rule.org_id,
        "created_by": rule.created_by,
        "name": rule.name,
        "provider": rule.provider.value,
        "enabled": rule.enabled,
        "prompt_template": rule.prompt_template,
        "session_mode": rule.session_mode.value,
        "rate_limit_per_minute": rule.rate_limit_per_minute,
        "max_body_bytes": rule.max_body_bytes,
        "secret_prefix": secret_prefix(decrypt_secret(rule.secret_encrypted)),
        "secret_rotated_at": rule.secret_rotated_at,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }
    if plaintext_secret is not None:
        # 明文 secret 只在创建/轮换响应中出现一次（管理员复制给发送方）。
        data["secret"] = plaintext_secret
    return data


def _delivery_data(delivery: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "rule_id": delivery.rule_id,
        "org_id": delivery.org_id,
        "idempotency_key": delivery.idempotency_key,
        "status": delivery.status.value,
        "payload_hash": delivery.payload_hash,
        "session_id": delivery.session_id,
        "error": delivery.error,
        "attempts": delivery.attempts,
        "next_attempt_at": delivery.next_attempt_at,
        "processing_started_at": delivery.processing_started_at,
        "received_at": delivery.received_at,
        "processed_at": delivery.processed_at,
    }


def _parse_provider(value: str | None) -> WebhookProvider | None:
    if value is None:
        return None
    try:
        return WebhookProvider(value)
    except ValueError:
        raise ValidationError("provider 仅支持 generic/github", field="provider") from None


def _parse_session_mode(value: str | None) -> WebhookSessionMode | None:
    if value is None:
        return None
    try:
        return WebhookSessionMode(value)
    except ValueError:
        raise ValidationError("session_mode 仅支持 new/reuse", field="session_mode") from None


# ── 管理 API（必须先于 /{rule_id} 注册，避免 "rules" 被当成 rule_id）──


@router.get("/rules", summary="Webhook 规则列表（组织管理员）")
async def list_rules(
    organization_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _resolve_org(current_user, organization_id)
    await require_org_admin(db, current_user, org_id)
    rows = (
        (
            await db.execute(
                select(WebhookRule)
                .where(WebhookRule.org_id == org_id)
                .order_by(WebhookRule.created_at.desc(), WebhookRule.id)
            )
        )
        .scalars()
        .all()
    )
    return {"success": True, "data": {"items": [_rule_data(rule) for rule in rows]}}


@router.post("/rules", summary="创建 Webhook 规则（组织管理员）")
async def create_rule(
    payload: RuleCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _resolve_org(current_user, payload.organization_id)
    await require_org_admin(db, current_user, org_id)
    validate_prompt_template(payload.prompt_template)

    plaintext = generate_secret()
    rule = WebhookRule(
        org_id=org_id,
        created_by=current_user.id,
        name=payload.name,
        provider=_parse_provider(payload.provider) or WebhookProvider.GENERIC,
        secret_encrypted=encrypt_secret(plaintext),
        enabled=payload.enabled,
        prompt_template=payload.prompt_template,
        session_mode=_parse_session_mode(payload.session_mode) or WebhookSessionMode.NEW,
        rate_limit_per_minute=payload.rate_limit_per_minute
        or settings.WEBHOOK_RATE_LIMIT_PER_MINUTE,
        max_body_bytes=payload.max_body_bytes or settings.WEBHOOK_MAX_BODY_BYTES,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await audit_service.log_activity(
        user_id=current_user.id,
        action="create_webhook_rule",
        target_type="webhook_rule",
        target_id=rule.id,
        detail=json.dumps({"name": rule.name, "org_id": rule.org_id}, ensure_ascii=False),
        request=request,
    )
    return {"success": True, "data": _rule_data(rule, plaintext_secret=plaintext)}


@router.patch("/rules/{rule_id}", summary="更新 Webhook 规则（组织管理员）")
async def update_rule(
    rule_id: str,
    payload: RuleUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await _load_rule_for_admin(db, current_user, rule_id)
    changes = payload.model_dump(exclude_unset=True)

    if "prompt_template" in changes:
        if changes["prompt_template"] is None:
            raise ValidationError("prompt_template 不能为空", field="prompt_template")
        validate_prompt_template(changes["prompt_template"])
        rule.prompt_template = changes["prompt_template"]
    if "name" in changes and changes["name"] is not None:
        rule.name = changes["name"]
    if "enabled" in changes and changes["enabled"] is not None:
        rule.enabled = bool(changes["enabled"])
    if "session_mode" in changes and changes["session_mode"] is not None:
        rule.session_mode = _parse_session_mode(changes["session_mode"])
    if "rate_limit_per_minute" in changes and changes["rate_limit_per_minute"] is not None:
        rule.rate_limit_per_minute = int(changes["rate_limit_per_minute"])
    if "max_body_bytes" in changes and changes["max_body_bytes"] is not None:
        rule.max_body_bytes = int(changes["max_body_bytes"])

    await db.commit()
    await db.refresh(rule)
    await audit_service.log_activity(
        user_id=current_user.id,
        action="update_webhook_rule",
        target_type="webhook_rule",
        target_id=rule.id,
        detail=json.dumps(
            {key: value for key, value in changes.items() if key != "prompt_template"},
            ensure_ascii=False,
            default=str,
        ),
        request=request,
    )
    return {"success": True, "data": _rule_data(rule)}


@router.delete("/rules/{rule_id}", summary="删除 Webhook 规则（组织管理员）")
async def delete_rule(
    rule_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await _load_rule_for_admin(db, current_user, rule_id)
    # 显式删子行：SQLite 测试路径未开外键级联，MySQL 侧 FK 亦有 ON DELETE CASCADE。
    await db.execute(delete(WebhookDelivery).where(WebhookDelivery.rule_id == rule.id))
    await db.delete(rule)
    await db.commit()
    await audit_service.log_activity(
        user_id=current_user.id,
        action="delete_webhook_rule",
        target_type="webhook_rule",
        target_id=rule_id,
        detail=None,
        request=request,
    )
    return {"success": True, "data": {"deleted": True, "id": rule_id}}


@router.post("/rules/{rule_id}/rotate-secret", summary="轮换 Webhook secret（组织管理员）")
async def rotate_secret(
    rule_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await _load_rule_for_admin(db, current_user, rule_id)
    plaintext = generate_secret()
    rule.secret_prev_encrypted = rule.secret_encrypted
    rule.secret_encrypted = encrypt_secret(plaintext)
    rule.secret_rotated_at = _utcnow()
    await db.commit()
    await db.refresh(rule)
    await audit_service.log_activity(
        user_id=current_user.id,
        action="rotate_webhook_secret",
        target_type="webhook_rule",
        target_id=rule.id,
        detail=None,
        request=request,
    )
    return {"success": True, "data": _rule_data(rule, plaintext_secret=plaintext)}


@router.post("/rules/{rule_id}/test", summary="dry-run：渲染模板 + 可选验签（不写投递）")
async def dry_run_rule(
    rule_id: str,
    payload: DryRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await _load_rule_for_admin(db, current_user, rule_id)

    signature_valid: bool | None = None
    signature_error: str | None = None
    if payload.signature_headers is not None:
        raw = json.dumps(payload.payload, ensure_ascii=False).encode("utf-8")
        try:
            verify_signature(raw, payload.signature_headers, active_secrets(rule))
            signature_valid = True
        except AuthenticationError as exc:
            signature_valid = False
            signature_error = exc.message

    rendered = render_prompt(rule, payload.payload, "dry-run")
    data: dict[str, Any] = {
        "rendered_prompt": rendered,
        "signature_valid": signature_valid,
    }
    if signature_error is not None:
        data["signature_error"] = signature_error
    return {"success": True, "data": data}


@router.get("/deliveries", summary="Webhook 投递列表（组织管理员）")
async def list_deliveries(
    rule_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    organization_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _resolve_org(current_user, organization_id)
    await require_org_admin(db, current_user, org_id)

    stmt = select(WebhookDelivery).where(WebhookDelivery.org_id == org_id)
    if rule_id:
        stmt = stmt.where(WebhookDelivery.rule_id == rule_id)
    if status is not None:
        parsed = _STATUS_BY_VALUE.get(status.lower())
        if parsed is None:
            raise ValidationError(
                "status 仅支持 received/processing/succeeded/failed", field="status"
            )
        stmt = stmt.where(WebhookDelivery.status == parsed)
    stmt = stmt.order_by(WebhookDelivery.received_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"success": True, "data": {"items": [_delivery_data(row) for row in rows]}}


# ── 入站端点（必须最后注册：/{rule_id} 会吞掉 /rules 之外的同段路径）──


@router.post("/{rule_id}", summary="接收 Webhook 投递（HMAC 验签）")
async def receive_webhook(
    rule_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.WEBHOOKS_ENABLED:
        # 功能关闭 = 不接收；与"规则不存在"同形，不泄露规则 id 是否真实。
        raise NotFoundError("Webhook 规则不存在")

    rule = await db.get(WebhookRule, rule_id)
    if rule is None or not rule.enabled:
        raise NotFoundError("Webhook 规则不存在")

    # 1) 取原始 body（有上限保护；Content-Length 超限直接 413，不读 stream）。
    limit = effective_max_body_bytes(rule)
    raw = await read_body_with_limit(request, limit)

    # 2) 先验签，再解析（禁止重序列化后验签）。
    verify_signature(raw, request.headers, active_secrets(rule))

    # 3) 解析 JSON（payload 存入 outbox，poller 渲染用）。
    try:
        payload_obj = json.loads(raw)
    except ValueError as exc:
        raise ValidationError("webhook body 必须是合法 JSON") from exc
    try:
        payload_text = raw.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover - json.loads 已接受，仅非 UTF-8 编码触发
        payload_text = json.dumps(payload_obj, ensure_ascii=False)

    # 4) 限流（按规则 + IP）；超限不得落投递行。429 需 Retry-After：AppError
    #    handler 不支持响应头，端点直接构造统一错误包裹。
    client_ip = extract_client_ip(request) or "unknown"
    try:
        await enforce_rate_limit(rule, client_ip)
    except RateLimitError as exc:
        retry_after = int((exc.detail or {}).get("retry_after", 60))
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "code": 429,
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": exc.message,
                "detail": exc.detail,
                "request_id": getattr(getattr(request, "state", None), "request_id", None),
                "data": None,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # 5) 幂等：Redis 快路径（不可用降级），DB 唯一约束最终仲裁。
    webhook_id = request.headers.get("webhook-id")
    idempotency_key = derive_idempotency_key(webhook_id, raw)
    await mark_idempotency_in_redis(rule.id, idempotency_key)

    result = await register_delivery(
        db,
        rule,
        idempotency_key=idempotency_key,
        payload_text=payload_text,
        payload_hash=hashlib.sha256(raw).hexdigest(),
    )
    return JSONResponse(
        status_code=result.http_status,
        content={"success": True, "data": result.to_data()},
    )
