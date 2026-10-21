"""LLM Provider configuration API — multi-config per provider, table-style management.

Stored in Redis as a hash set keyed by config ID.
Key pattern: `agent:llm_configs` → hash of {config_id: json_data}
Each config has a unique ID (uuid), provider, and optional is_default flag.
Only one config per provider can be is_default at a time.

Inspired by PaiAgent's llm_global_config database table.

DEPRECATED（B3 死设定清理）：工作流编辑器入口与「使用全局配置」开关已移除，
且工作流执行引擎从未消费本存储。全部端点保留并标记 deprecated，待 B4 的
L1 Provider 统一层落地后删除；请勿新增调用方。
"""

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.core.redis import redis_client
from app.core.security import get_current_user, is_platform_admin
from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

REDIS_KEY = "agent:llm_configs"
SUPPORTED_PROVIDERS = {"openai", "deepseek", "qwen", "zhipu", "step", "ai_ping", "llm"}
PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "zhipu": "智谱",
    "step": "阶跃星辰",
    "ai_ping": "AI平",
    "llm": "通用LLM",
}


def _mask_secret(value: str) -> str:
    """Mask a secret while keeping a short prefix/suffix for identification."""
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def _public_config(data: dict[str, Any]) -> dict[str, Any]:
    """Return a config with the API key masked for client responses."""
    out = dict(data)
    # 损坏配置的原始值仅留在服务端存储（可能含半写的 api_key），绝不出网（#301）。
    out.pop("_raw_value", None)
    if out.get("api_key"):
        out["api_key"] = _mask_secret(str(out["api_key"]))
    return out


# ── Schemas ────────────────────────────────────────────────────────────


class LLMConfigCreate(BaseModel):
    """Create a new LLM configuration."""

    provider: str = Field(..., description="Provider identifier")
    config_name: str = Field(..., max_length=100, description="Human-readable alias")
    api_key: str = Field(..., description="API key")
    api_url: str = Field("", description="Base URL")
    model: str = Field("", description="Default model name")
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    is_default: bool = Field(False)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"不支持的 provider: {v}. 支持: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
            )
        return v


class LLMConfigUpdate(BaseModel):
    """Partial update for an existing config."""

    config_name: str | None = None
    api_key: str | None = None
    api_url: str | None = None
    model: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    is_default: bool | None = None
    provider: str | None = Field(None, description="Change provider")


# ── Redis helpers ───────────────────────────────────────────────────────


def _corrupt_placeholder(config_id: str, raw: str | None, reason: str) -> dict[str, Any]:
    """损坏配置的占位条目（issue #301）：条目不消失、显式标记 invalid。

    此前坏值被 `except json.JSONDecodeError: continue` 静默丢弃——条目凭空
    消失、total 少 1、零日志。现在保留占位条目：列表里可见（invalid=true）、
    可被删除或 **PUT 全量重写**修复（评审 m1：PATCH 部分更新不清除
    invalid/invalid_reason 标记，不构成修复路径）；原始值仅存服务端字段
    `_raw_value`（`_public_config` 永不外发），供运维排查。
    """
    entry: dict[str, Any] = {
        "id": config_id,
        "config_name": f"[损坏] {config_id}",
        "provider": None,
        "api_key": "",
        "invalid": True,
        "invalid_reason": reason,
    }
    if raw is not None:
        entry["_raw_value"] = raw
    return entry


async def _load_all() -> dict[str, dict[str, Any]]:
    """Load all configs from Redis hash. Returns {config_id: data}.

    损坏的值（非 UTF-8 / 非 JSON / 非对象）不再静默丢弃：保留占位条目并
    warning 留痕（issue #301），使故障可观测、可修复。
    """
    if not redis_client:
        return {}
    raw = await redis_client.hgetall(REDIS_KEY)
    if not raw:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        reason: str | None = None
        data: dict[str, Any] | None = None
        raw_text: str | None = None
        try:
            if isinstance(v, bytes):
                v = v.decode("utf-8")
            raw_text = v
            parsed = json.loads(v)
            if not isinstance(parsed, dict):
                raise ValueError(f"值为 JSON {type(parsed).__name__}，不是配置对象")
            data = parsed
        except (UnicodeDecodeError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
        if reason is not None:
            logger.warning(
                "LLM 配置 '%s' 的值损坏（%s）——保留占位条目（invalid=true），不静默丢弃",
                k,
                reason,
            )
            result[k] = _corrupt_placeholder(k, raw_text, reason)
        elif data is not None:
            result[k] = data
    return result


async def _save_all(configs: dict[str, dict[str, Any]]) -> None:
    """Save all configs to Redis hash."""
    if not redis_client:
        raise ValidationError("Redis 不可用")
    # Build the hash mapping
    mapping: dict[str, str] = {}
    for cid, data in configs.items():
        mapping[cid] = json.dumps(data, ensure_ascii=False)
    # Replace entire hash
    await redis_client.delete(REDIS_KEY)
    if mapping:
        await redis_client.hset(REDIS_KEY, mapping=mapping)
    # 30 day TTL
    await redis_client.expire(REDIS_KEY, 86400 * 30)


async def _clear_default_for_provider(provider: str, configs: dict[str, dict[str, Any]]) -> None:
    """Unset is_default on all configs for the given provider."""
    for _cid, data in configs.items():
        if data.get("provider") == provider:
            data["is_default"] = False


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("", deprecated=True)
async def list_configs(
    current_user: User = Depends(get_current_user),
):
    """List all LLM configurations. Optionally filter by provider.

    Query params:
        provider: filter by provider (optional)

    W0-2：C5 删除前的过渡期，配置读端点只允许平台管理员（显式
    ``is_superuser``）；其余账号 403。拒绝必须发生在读取 Redis 之前。
    """
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()
    configs = [_public_config(v) for v in all_configs.values()]

    return {
        "success": True,
        "data": configs,
        "total": len(configs),
    }


@router.get("/providers", deprecated=True)
async def list_providers(
    current_user: User = Depends(get_current_user),
):
    """List supported providers with labels."""
    return {
        "success": True,
        "data": [
            {"key": k, "label": PROVIDER_LABELS.get(k, k)} for k in sorted(SUPPORTED_PROVIDERS)
        ],
    }


@router.get("/default/{provider}", deprecated=True)
async def get_default_config(
    provider: str,
    current_user: User = Depends(get_current_user),
):
    """Get the default config for a provider. Returns 404 if none.

    W0-2：过渡期只允许平台管理员（见 ``list_configs``）。
    """
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    provider = provider.strip().lower()
    all_configs = await _load_all()
    for cid, data in all_configs.items():
        if data.get("provider") == provider and data.get("is_default"):
            public = _public_config(data)
            public["id"] = cid
            return {"success": True, "data": public}
    raise NotFoundError(f"Provider '{provider}' 没有默认配置")


@router.get("/detail/{config_id}", deprecated=True)
async def get_config_detail(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a single config by ID.

    W0-2：过渡期只允许平台管理员（见 ``list_configs``）。
    """
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")
    data = all_configs[config_id]
    public = _public_config(data)
    public["id"] = config_id
    return {"success": True, "data": public}


@router.post("", deprecated=True)
async def create_config(
    body: LLMConfigCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a new LLM configuration. Returns the created config with ID."""
    # 写操作仅限平台管理员（W0-1：只认显式 is_superuser，不再看 users.role）
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()

    config_id = uuid.uuid4().hex[:12]
    data = body.model_dump()
    data["id"] = config_id
    data["created_at"] = None  # will be set on first read as ISO string

    if data.get("is_default"):
        await _clear_default_for_provider(data["provider"], all_configs)

    all_configs[config_id] = data
    await _save_all(all_configs)
    logger.info(
        "LLM config '%s' created by user %d (provider=%s, id=%s)",
        data["config_name"],
        current_user.id,
        data["provider"],
        config_id,
    )

    return {
        "success": True,
        "data": _public_config(data),
        "message": f"配置 '{body.config_name}' 已创建",
    }


@router.put("/{config_id}", deprecated=True)
async def update_config(
    config_id: str,
    body: LLMConfigCreate,
    current_user: User = Depends(get_current_user),
):
    """Full update of an existing config."""
    # 写操作仅限平台管理员（W0-1：只认显式 is_superuser，不再看 users.role）
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")

    data = body.model_dump()

    if data.get("is_default"):
        await _clear_default_for_provider(data["provider"], all_configs)

    data["id"] = config_id
    all_configs[config_id] = data
    await _save_all(all_configs)

    return {
        "success": True,
        "data": _public_config(data),
        "message": f"配置 '{body.config_name}' 已更新",
    }


@router.patch("/{config_id}", deprecated=True)
async def patch_config(
    config_id: str,
    body: LLMConfigUpdate,
    current_user: User = Depends(get_current_user),
):
    """Partial update of a config."""
    # 写操作仅限平台管理员（W0-1：只认显式 is_superuser，不再看 users.role）
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")

    existing = all_configs[config_id]
    update_data = body.model_dump(exclude_unset=True)

    if update_data.get("is_default"):
        await _clear_default_for_provider(
            update_data.get("provider") or existing["provider"], all_configs
        )

    existing.update(update_data)
    await _save_all(all_configs)

    existing["id"] = config_id
    return {
        "success": True,
        "data": _public_config(existing),
        "message": "配置已更新",
    }


@router.delete("/{config_id}", deprecated=True)
async def delete_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Delete a config by ID."""
    # 写操作仅限平台管理员（W0-1：只认显式 is_superuser，不再看 users.role）
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    if not redis_client:
        raise ValidationError("Redis 不可用")

    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")

    name = all_configs[config_id].get("config_name", config_id)
    del all_configs[config_id]
    await _save_all(all_configs)

    return {
        "success": True,
        "message": f"配置 '{name}' 已删除",
    }


@router.post("/{config_id}/default", deprecated=True)
async def set_default_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Set a config as default for its provider. Unsets other defaults."""
    # 写操作仅限平台管理员（W0-1：只认显式 is_superuser，不再看 users.role）
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")

    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")

    provider = all_configs[config_id]["provider"]
    await _clear_default_for_provider(provider, all_configs)
    all_configs[config_id]["is_default"] = True
    await _save_all(all_configs)

    return {
        "success": True,
        "message": f"已将 '{all_configs[config_id]['config_name']}' 设为 {provider} 的默认配置",
    }
