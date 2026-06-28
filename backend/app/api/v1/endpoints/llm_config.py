"""LLM Provider configuration API — multi-config per provider, table-style management.

Stored in Redis as a hash set keyed by config ID.
Key pattern: `agent:llm_configs` → hash of {config_id: json_data}
Each config has a unique ID (uuid), provider, and optional is_default flag.
Only one config per provider can be is_default at a time.

Inspired by PaiAgent's llm_global_config database table.
"""

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.core.redis import redis_client
from app.core.security import get_current_user
from app.exceptions import NotFoundError, ValidationError
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
            raise ValueError(f"不支持的 provider: {v}. 支持: {', '.join(sorted(SUPPORTED_PROVIDERS))}")
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


async def _load_all() -> dict[str, dict[str, Any]]:
    """Load all configs from Redis hash. Returns {config_id: data}."""
    if not redis_client:
        return {}
    raw = await redis_client.hgetall(REDIS_KEY)
    if not raw:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        try:
            result[k] = json.loads(v)
        except json.JSONDecodeError:
            continue
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
    for cid, data in configs.items():
        if data.get("provider") == provider:
            data["is_default"] = False


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("")
async def list_configs(
    current_user: User = Depends(get_current_user),
):
    """List all LLM configurations. Optionally filter by provider.

    Query params:
        provider: filter by provider (optional)
    """
    from fastapi import Query

    all_configs = await _load_all()
    configs = list(all_configs.values())

    return {
        "success": True,
        "data": configs,
        "total": len(configs),
    }


@router.get("/providers")
async def list_providers(
    current_user: User = Depends(get_current_user),
):
    """List supported providers with labels."""
    return {
        "success": True,
        "data": [
            {"key": k, "label": PROVIDER_LABELS.get(k, k)}
            for k in sorted(SUPPORTED_PROVIDERS)
        ],
    }


@router.get("/default/{provider}")
async def get_default_config(
    provider: str,
    current_user: User = Depends(get_current_user),
):
    """Get the default config for a provider. Returns 404 if none."""
    provider = provider.strip().lower()
    all_configs = await _load_all()
    for cid, data in all_configs.items():
        if data.get("provider") == provider and data.get("is_default"):
            data["id"] = cid
            return {"success": True, "data": data}
    raise NotFoundError(f"Provider '{provider}' 没有默认配置")


@router.get("/detail/{config_id}")
async def get_config_detail(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a single config by ID."""
    all_configs = await _load_all()
    if config_id not in all_configs:
        raise NotFoundError(f"配置 '{config_id}' 不存在")
    data = all_configs[config_id]
    data["id"] = config_id
    return {"success": True, "data": data}


@router.post("")
async def create_config(
    body: LLMConfigCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a new LLM configuration. Returns the created config with ID."""
    all_configs = await _load_all()

    config_id = uuid.uuid4().hex[:12]
    data = body.model_dump()
    data["id"] = config_id
    data["created_at"] = None  # will be set on first read as ISO string

    if data.get("is_default"):
        await _clear_default_for_provider(data["provider"], all_configs)

    all_configs[config_id] = data
    await _save_all(all_configs)
    logger.info("LLM config '%s' created by user %d (provider=%s, id=%s)",
                data["config_name"], current_user.id, data["provider"], config_id)

    return {
        "success": True,
        "data": data,
        "message": f"配置 '{body.config_name}' 已创建",
    }


@router.put("/{config_id}")
async def update_config(
    config_id: str,
    body: LLMConfigCreate,
    current_user: User = Depends(get_current_user),
):
    """Full update of an existing config."""
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
        "data": data,
        "message": f"配置 '{body.config_name}' 已更新",
    }


@router.patch("/{config_id}")
async def patch_config(
    config_id: str,
    body: LLMConfigUpdate,
    current_user: User = Depends(get_current_user),
):
    """Partial update of a config."""
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
        "data": existing,
        "message": "配置已更新",
    }


@router.delete("/{config_id}")
async def delete_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Delete a config by ID."""
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


@router.post("/{config_id}/default")
async def set_default_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
):
    """Set a config as default for its provider. Unsets other defaults."""
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
