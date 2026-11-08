"""Per-user LLM provider settings — validation, encryption, and resolution.

个人设置页「AI 模型设置」的真实生效链路（本模块是唯一解析入口）：

- 保存：``hashed`` 的 Fernet 密钥加密 API key（密钥由服务端 ``SECRET_KEY``
  确定性派生），落库到 ``UserSettings.preferences["ai_provider"]``（JSON，无迁移）；
- 读取：``read_api_key`` 兼容历史明文（无前缀）与 ``fernet:v1:`` 密文；
- 解析：``resolve_for_user`` 返回 per-request 的 ``AsyncOpenAI`` 客户端与模型——
  用户配置优先、无效/缺失回退服务端默认（``app.dependencies._build_openai_client``）。

安全约束：
- API key 绝不出现在日志/响应中（响应只给掩码）；
- 客户端缓存按配置哈希做键（API key 只参与哈希，不落缓存键明文），
  用户改 key/base_url 后自然得到新客户端；
- ``SECRET_KEY`` 轮换后旧密文无法解密 → 视为无效配置并回退服务端默认，
  不阻断聊天（用户重存一次即可）。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import socket
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm_failures import mark_user_llm_client
from app.models.user_settings import UserSettings

logger = logging.getLogger(__name__)

# ── Provider 目录（GET /settings/providers 的唯一事实源）────────────────────

AVAILABLE_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-v4-flash", "deepseek-reasoner"],
        "requires_key": True,
    },
    {
        "id": "ollama",
        "name": "Ollama (本地)",
        "base_url": "http://localhost:11434/v1",
        "models": ["llama3", "qwen2.5", "deepseek-r1", "bge-m3"],
        "requires_key": False,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        "requires_key": True,
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-flash", "glm-4-plus"],
        "requires_key": True,
    },
    {
        "id": "custom",
        "name": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "models": [],
        "requires_key": True,
    },
]

_PROVIDERS_BY_ID = {p["id"]: p for p in AVAILABLE_PROVIDERS}

# SSRF 纵深防御：拒绝云元数据/链路本地地址（含 IPv4-mapped/数字型/DNS 别名），
# 并关闭 OpenAI 客户端重定向——302 到 IMDS 是最常见的绕过方式。
# 私网/本机地址保留（Ollama 自建场景），完整内网隔离由部署网络策略承担。
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata"}
_BLOCKED_NETS = (ipaddress.ip_network("169.254.0.0/16"),)
_BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip in _BLOCKED_IPS
        or ip.is_link_local
        or (ip.version == 4 and any(ip in net for net in _BLOCKED_NETS))
    )


def _check_host(host: str, port: int | None) -> None:
    """解析主机并拒绝元数据/链路本地目标；私网与 loopback 保留。"""
    host = host.rstrip(".").lower()
    if not host or host in _BLOCKED_HOSTNAMES:
        raise LLMConfigError("Base URL 指向不允许的主机")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked_ip(literal):
            raise LLMConfigError("Base URL 指向不允许的地址")
        return
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LLMConfigError("Base URL 主机无法解析") from exc
    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            resolved.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    if not resolved:
        raise LLMConfigError("Base URL 主机无法解析")
    if any(_blocked_ip(ip) for ip in resolved):
        raise LLMConfigError("Base URL 指向不允许的地址")


def _check_host_structure(host: str, port: int | None) -> None:
    """不解析 DNS 的结构校验（运行期每次请求用，避免同步 DNS 阻塞事件循环）。"""
    host = host.rstrip(".").lower()
    if not host or host in _BLOCKED_HOSTNAMES:
        raise LLMConfigError("Base URL 指向不允许的主机")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return
    if _blocked_ip(literal):
        raise LLMConfigError("Base URL 指向不允许的地址")


_KEY_PREFIX = "fernet:v1:"
_MAX_CACHED_CLIENTS = 64


class LLMConfigError(ValueError):
    """用户 LLM 配置非法（文案面向 API 调用方，绝不包含密钥材料）。"""


# ── API key 加密（Fernet，密钥由 SECRET_KEY 确定性派生）──────────────────────


@lru_cache(maxsize=4)
def _fernet_for(secret: str):
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"docmind:user-llm-provider-key:v1:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet():
    return _fernet_for(settings.SECRET_KEY)


def encrypt_api_key(plain: str) -> str:
    """加密并加版本前缀；明文绝不落库。"""
    token = _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_KEY_PREFIX}{token}"


def decrypt_api_key(stored: str) -> str:
    """解密 ``fernet:v1:`` 密文；历史明文原样返回（向后兼容）。"""
    if not stored:
        return ""
    if not stored.startswith(_KEY_PREFIX):
        return stored
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(stored[len(_KEY_PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise LLMConfigError(
            "已保存的 API Key 无法解密（服务端 SECRET_KEY 可能已轮换），请重新保存模型设置"
        ) from exc


def read_api_key(config: dict[str, Any]) -> str:
    """读取用户配置中的 API key（新密文字段优先，兼容历史明文 ``api_key``）。

    旧 UI 曾把 GET 返回的掩码（``sk-a...zzzz``/``********``）随 preferences
    落库；掩码不是密钥，必须视为未配置，让 resolver 回退服务端默认。
    """
    encrypted = config.get("api_key_encrypted")
    if isinstance(encrypted, str) and encrypted:
        return decrypt_api_key(encrypted)
    legacy = config.get("api_key")
    if isinstance(legacy, str) and legacy:
        if is_masked_placeholder(legacy):
            return ""
        return legacy
    return ""


def mask_api_key(plain: str) -> str:
    """响应/日志中只暴露掩码形态。空 key 返回空串（前端据此提示未配置）。"""
    if not plain:
        return ""
    if len(plain) <= 8:
        return "********"
    return f"{plain[:4]}...{plain[-4:]}"


def is_masked_placeholder(value: str) -> bool:
    """判断前端回传的 key 是否为掩码占位（掩码回写必须保留已存 key）。"""
    if not value:
        return False
    return "..." in value or "*" in value


# ── 校验 ────────────────────────────────────────────────────────────────────


def _normalize_base_url(raw: str | None, *, resolve_host: bool = False) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise LLMConfigError("Base URL 格式不合法（端口或 IPv6 无效）") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LLMConfigError("Base URL 必须包含 http(s) 协议与主机名")
    host = parsed.hostname or ""
    if parsed.username or parsed.password:
        raise LLMConfigError("Base URL 不得包含用户名/密码")
    if parsed.query or parsed.fragment:
        raise LLMConfigError("Base URL 不得包含查询参数或锚点")
    if resolve_host:
        _check_host(host, port)
    else:
        _check_host_structure(host, port)
    return url.rstrip("/")


def validate_provider_config(
    provider: str | None,
    model: str | None,
    base_url: str | None,
    *,
    resolve_host: bool = False,
) -> tuple[str, str, str]:
    """校验并规范化用户提交的 provider/model/base_url，返回三元组。

    允许任意 OpenAI 兼容模型名（自建部署的模型清单远超内置目录），
    但 provider 必须在目录内、模型名非空且无控制字符。
    ``resolve_host=True`` 仅在保存路径使用（DNS 解析检查元数据地址）；
    运行期解析配置只做结构校验，避免每个请求同步 DNS 阻塞事件循环。
    """
    pid = (provider or "").strip()
    if pid not in _PROVIDERS_BY_ID:
        raise LLMConfigError(f"不支持的 Provider: {pid or '(空)'}")
    model_name = (model or "").strip()
    if not model_name:
        raise LLMConfigError("模型名称不能为空")
    if len(model_name) > 200 or any(ord(ch) < 32 or ord(ch) == 127 for ch in model_name):
        raise LLMConfigError("模型名称非法（超长或包含控制字符）")
    normalized_base = _normalize_base_url(base_url, resolve_host=resolve_host)
    if pid == "custom" and not normalized_base:
        raise LLMConfigError("自定义 Provider 必须填写 Base URL")
    return pid, model_name, normalized_base


def provider_requires_key(provider: str) -> bool:
    return bool(_PROVIDERS_BY_ID.get(provider, {}).get("requires_key", True))


def provider_default_base_url(provider: str) -> str:
    return str(_PROVIDERS_BY_ID.get(provider, {}).get("base_url") or "")


# ── 客户端构造与缓存 ────────────────────────────────────────────────────────

_CLIENT_CACHE: OrderedDict[str, AsyncOpenAI] = OrderedDict()


def clear_client_cache() -> None:
    """测试/密钥轮换时显式失效缓存。"""
    _CLIENT_CACHE.clear()


def _new_client(provider: str, base_url: str, api_key: str) -> AsyncOpenAI:
    # 关闭重定向：允许的 host 302 到云元数据端点是 SSRF 常见绕过。
    http_client = DefaultAsyncHttpxClient(follow_redirects=False)
    if provider == "ollama":
        # Ollama 的 OpenAI 兼容端点不校验 key，但 SDK 要求非空字符串。
        client = AsyncOpenAI(
            api_key=api_key or "ollama", base_url=base_url, http_client=http_client
        )
    else:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    # Agent/RAG 共用标记：失败时据此给「检查我的模型配置」文案，而不是
    # 通用系统错误，也不把它算进服务端模型的熔断计数。
    mark_user_llm_client(client)
    return client


def _cached_client(provider: str, base_url: str, cache_id: str, api_key: str) -> AsyncOpenAI:
    """按保存时生成的随机 ``config_id`` 缓存客户端（连接池复用）。

    缓存键不含 API key（也无需对 key 做哈希）：每次保存都会生成新的
    ``config_id``，key/base_url 变更必然得到新客户端。历史配置没有
    ``config_id`` 时不缓存，避免以任何形式指纹化密钥。
    """
    if not cache_id:
        return _new_client(provider, base_url, api_key)
    fingerprint = f"{provider}|{base_url}|{cache_id}"
    cached = _CLIENT_CACHE.get(fingerprint)
    if cached is not None:
        _CLIENT_CACHE.move_to_end(fingerprint)
        return cached
    client = _new_client(provider, base_url, api_key)
    _CLIENT_CACHE[fingerprint] = client
    while len(_CLIENT_CACHE) > _MAX_CACHED_CLIENTS:
        _CLIENT_CACHE.popitem(last=False)
    return client


def build_user_llm_client(config: dict[str, Any]) -> tuple[AsyncOpenAI, str, str, str, str]:
    """把已保存的配置构造为 (client, model, provider, base_url, api_key)。

    Raises:
        LLMConfigError: 配置非法/缺 key/无法解密。
    """
    provider, model, base_url = validate_provider_config(
        config.get("provider"), config.get("model"), config.get("base_url")
    )
    api_key = read_api_key(config)
    if provider_requires_key(provider) and not api_key:
        raise LLMConfigError(f"{provider} 需要 API Key，但用户未配置")
    effective_base = base_url or provider_default_base_url(provider)
    if not effective_base:
        raise LLMConfigError("缺少 Base URL")
    client = _cached_client(provider, effective_base, str(config.get("config_id") or ""), api_key)
    return client, model, provider, effective_base, api_key


# ── 解析（唯一入口：Chat/RAG 与 Agent 共用）────────────────────────────────


@dataclass(frozen=True)
class LLMResolution:
    """一次请求的 LLM 解析结果。source=user 时 client/model 来自个人设置。"""

    client: AsyncOpenAI | None
    model: str
    source: str  # "user" | "server"
    provider: str | None = None
    base_url: str | None = None
    has_api_key: bool = False


def server_default_model() -> str:
    return settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL


def cache_scope(resolution: LLMResolution, user_id: int | None = None) -> str:
    """回答级语义缓存分域：个人配置按用户+provider+model，服务端按模型。

    同组织的不同用户/模型不得互相命中缓存答案（个人模型上线后的正确性要求）。
    """
    if resolution.source == "user":
        return f"user:{user_id}:{resolution.provider}:{resolution.model}"
    return f"server:{resolution.model}"


def _server_default_resolution() -> LLMResolution:
    from app.dependencies import _build_openai_client

    return LLMResolution(
        client=_build_openai_client(),
        model=server_default_model(),
        source="server",
    )


async def get_saved_provider_config(
    db: AsyncSession, user=None, *, user_id: int | None = None
) -> dict[str, Any] | None:
    """读取用户已保存的 provider 配置（未保存/形状漂移 → None）。"""
    uid = user_id if user_id is not None else getattr(user, "id", None)
    if uid is None:
        return None
    row = (
        await db.execute(select(UserSettings).where(UserSettings.user_id == uid))
    ).scalar_one_or_none()
    if row is None or not isinstance(row.preferences, dict):
        return None
    raw = row.preferences.get("ai_provider")
    if not isinstance(raw, dict) or not raw.get("provider"):
        return None
    return dict(raw)


async def resolve_for_user(
    db: AsyncSession, user=None, *, user_id: int | None = None
) -> LLMResolution:
    """解析当前请求应使用的 LLM 客户端与模型。

    优先级：有效的个人配置（source=user）> 服务端默认（source=server）。
    用户配置非法（含 key 无法解密/缺 key）时回退服务端默认并留 warning——
    绝不因个人配置问题阻断聊天；运行时鉴权失败（key 被上游拒绝）仍按真实
    错误上报，不做静默回退（否则用户无法发现配置错了）。
    """
    uid = user_id if user_id is not None else getattr(user, "id", None)
    try:
        saved = await get_saved_provider_config(db, user, user_id=uid)
    except Exception as exc:  # noqa: BLE001 - 读取失败不应阻断聊天
        logger.warning(
            "读取用户 LLM 设置失败，回退服务端默认（user_id=%s, error=%s）",
            uid,
            type(exc).__name__,
        )
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 - 回滚失败也不能阻断聊天
            pass
        return _server_default_resolution()

    if saved:
        try:
            client, model, provider, base_url, api_key = build_user_llm_client(saved)
            return LLMResolution(
                client=client,
                model=model,
                source="user",
                provider=provider,
                base_url=base_url,
                has_api_key=bool(api_key),
            )
        except LLMConfigError as exc:
            logger.warning(
                "用户 LLM 配置无效，回退服务端默认（user_id=%s, provider=%s, reason=%s）",
                uid,
                saved.get("provider"),
                str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - 客户端构造失败同样回退
            logger.warning(
                "用户 LLM 客户端构造失败，回退服务端默认（user_id=%s, error=%s）",
                uid,
                type(exc).__name__,
            )

    return _server_default_resolution()
