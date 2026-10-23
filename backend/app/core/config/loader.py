"""配置加载器：base → bundles → profile → patch → 环境层（issue #479）。

合并优先级（低 → 高）::

    代码默认值 < config/base.yaml < profile 声明的 bundles（按序）
    < config/profiles/<profile>.yaml < --patch（按 CLI 顺序） < 环境层

环境层内部：``.env`` 文件 < 真实环境变量（pydantic-settings 的既有语义）。
把 ``.env`` 归入环境层（而不是 YAML 之上、env 之下的独立层）是有意裁定：
现状部署把 100+ 变量放在 ``backend/.env``，若发行版 YAML 能压过它，部署方
的显式配置会被静默覆盖（与 issue #232 同类问题）。

加载结果缓存；``reset_settings()`` 供测试清理。模块级 ``settings`` 是惰性
代理（:mod:`app.core.config.proxy`），import 不触发本模块的任何 I/O。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.core.config import Settings
from app.core.config.ai import _auto_tier_defaults
from app.core.config.schema import (
    ConfigError,
    PatchOp,
    ProfileSpec,
    apply_patches,
    deep_merge,
    validate_profile_name,
    validate_settings_map,
)

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]
# 测试可 monkeypatch 本常量指向临时目录（loader 内所有文件定位都经它）。
CONFIG_DIR = BACKEND_DIR / "config"

_SECRET_SUFFIXES = ("_KEY", "_SECRET", "_PASSWORD", "_TOKEN")
_URL_SECRET_FIELDS = ("DATABASE_URL", "REDIS_URL")
_URL_PASSWORD_RE = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")

_settings: Settings | None = None
_active_profile: str | None = None
_active_spec: ProfileSpec | None = None
_active_patches: list[str] = []


# ──────────────────────────────────────────────────────────────
# YAML 读取与分层
# ──────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(str(path), f"配置文件不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(str(path), f"无法解析 YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(str(path), "YAML 顶层必须是映射（mapping）")
    return data


def _load_layer_settings(path: Path) -> dict[str, Any]:
    """读取一层 YAML 的 ``settings:`` 映射并校验（键闭集 + secret 拒绝）。"""
    data = _read_yaml(path)
    settings_map = data.get("settings", {})
    if not isinstance(settings_map, dict):
        raise ConfigError(f"{path}.settings", "settings 必须是映射（mapping）")
    validate_settings_map(settings_map, origin=str(path))
    return settings_map


def _load_profile_spec(profile: str) -> ProfileSpec:
    path = CONFIG_DIR / "profiles" / f"{profile}.yaml"
    data = _read_yaml(path)
    try:
        return ProfileSpec.model_validate(data)
    except ConfigError:
        raise
    except ValidationError as exc:
        raise _validation_error_to_config_error(
            exc, path_prefix="profile", origin=str(path)
        ) from exc


def _load_patches(paths: list[str]) -> list[PatchOp]:
    ops: list[PatchOp] = []
    for raw_path in paths:
        path = Path(raw_path)
        data = _read_yaml(path)
        raw_ops = data.get("patch", [])
        if not isinstance(raw_ops, list):
            raise ConfigError(str(path), "patch 必须是列表")
        for index, item in enumerate(raw_ops):
            if not isinstance(item, dict):
                raise ConfigError(f"{path}[{index}]", "patch 项必须是映射（mapping）")
            try:
                ops.append(PatchOp.model_validate(item))
            except ConfigError:
                raise
            except ValidationError as exc:
                raise _validation_error_to_config_error(
                    exc, path_prefix=f"{path}[{index}]", origin=str(path)
                ) from exc
    return ops


def _validation_error_to_config_error(
    exc: ValidationError, *, path_prefix: str, origin: str
) -> ConfigError:
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
        message = str(first.get("msg", exc))
    else:  # pragma: no cover - pydantic 的 ValidationError 至少含一条 error
        loc, message = "", str(exc)
    path = f"{path_prefix}.{loc}" if loc else path_prefix
    return ConfigError(path, f"{origin}: {message}")


# ──────────────────────────────────────────────────────────────
# 环境层
# ──────────────────────────────────────────────────────────────


def _environment_layer() -> dict[str, Any]:
    """用一次纯环境构造找出「环境层显式设置」的字段及其值。

    ``model_fields_set`` 同时覆盖 ``.env`` 与真实环境变量（后者优先），
    这正是本 issue 想要的「``.env`` 属于环境层」语义。
    """
    try:
        env_settings = Settings()
    except ValidationError as exc:
        raise _validation_error_to_config_error(
            exc, path_prefix="settings", origin="environment"
        ) from exc
    return {name: getattr(env_settings, name) for name in env_settings.model_fields_set}


# ──────────────────────────────────────────────────────────────
# 构造与硬件档位
# ──────────────────────────────────────────────────────────────


def _build_settings(values: dict[str, Any]) -> Settings:
    try:
        return Settings(**values)
    except ValidationError as exc:
        raise _validation_error_to_config_error(
            exc, path_prefix="settings", origin="merged config"
        ) from exc


def _apply_hardware_tier(settings_obj: Settings) -> None:
    """对最终对象中「所有来源都未显式设置」的字段套用硬件档位默认值。

    应用过的字段以 ``hardware_tier_applied=[...]`` 打进启动日志。只对
    ``model_fields_set`` 之外的字段生效——这与 issue #232 的教训一致：
    ``.env`` 显式配置不得被硬件探测静默覆盖。
    """
    try:
        detected = _auto_tier_defaults(
            explicitly_set=settings_obj.model_fields_set,
            local_embedding=bool(settings_obj.ENABLE_LOCAL_EMBEDDING),
        )
    except Exception:  # pragma: no cover - 硬件探测失败时保持默认值
        return
    applied: list[str] = []
    for key, value in detected.items():
        if key in settings_obj.model_fields_set or not hasattr(settings_obj, key):
            continue
        object.__setattr__(settings_obj, key, value)
        applied.append(key)
    if applied:
        logger.info("hardware_tier_applied=%s", sorted(applied))


# ──────────────────────────────────────────────────────────────
# 打码与 hash
# ──────────────────────────────────────────────────────────────


def _mask_url_password(value: str) -> str:
    return _URL_PASSWORD_RE.sub(r"\1***\3", value)


def mask_secrets(values: dict[str, Any]) -> dict[str, Any]:
    """返回打码后的新映射：secret 字段 → ``***``；URL 密码段 → ``***``。"""
    masked: dict[str, Any] = {}
    for key, value in values.items():
        if key.endswith(_SECRET_SUFFIXES) and isinstance(value, str) and value:
            masked[key] = "***"
        elif key in _URL_SECRET_FIELDS and isinstance(value, str) and value:
            masked[key] = _mask_url_password(value)
        else:
            masked[key] = value
    return masked


def config_hash(payload: dict[str, Any]) -> str:
    """配置组装 hash（sha256 前 12 位）；入参必须已打码。

    约定入参为 ``{"profile": ..., "bundles": [...], "settings": {...}}``，
    使不同 profile/bundle 组合即使 settings 相同也能区分。
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ──────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────


def load_settings(profile: str | None = None, patches: list[str] | None = None) -> Settings:
    """按优先级合并并构造 ``Settings``；结果缓存。``profile=None`` → ``web``。"""
    global _settings, _active_profile, _active_spec, _active_patches

    name = validate_profile_name(profile or "web")
    requested_patches = list(patches or [])
    if _settings is not None:
        if _active_profile != name:
            raise ConfigError(
                f"profile.{name}",
                f"配置已按 profile={_active_profile} 加载，同一进程不能切换 profile",
            )
        # patches=None 表示「调用方未指定」，不参与一致性检查（如 build_app 的内部调用）；
        # 显式传入的 patch 列表与已应用的不一致时必须失败，绝不静默返回旧对象。
        if patches is not None and requested_patches != _active_patches:
            raise ConfigError(
                "patch",
                "配置已加载，本次传入的 patch 列表与已应用的不一致"
                f"（已应用 {_active_patches}，本次 {requested_patches}）",
            )
        return _settings

    spec = _load_profile_spec(name)

    merged = _load_layer_settings(CONFIG_DIR / "base.yaml")
    for bundle in spec.bundles:
        merged = deep_merge(merged, _load_layer_settings(CONFIG_DIR / "bundles" / f"{bundle}.yaml"))
    merged = deep_merge(merged, spec.settings)

    merged = apply_patches(merged, _load_patches(requested_patches))
    # 补丁可以 insert 新键：最终 YAML 层键必须仍落在 Settings.model_fields 内
    # （否则 Settings 的 extra="ignore" 会静默吞掉拼写错误）。
    validate_settings_map(merged, origin="merged profile")

    merged = deep_merge(merged, _environment_layer())
    settings_obj = _build_settings(merged)
    _apply_hardware_tier(settings_obj)

    _settings = settings_obj
    _active_profile = name
    _active_spec = spec
    _active_patches = requested_patches

    assembly = {
        "profile": name,
        "bundles": list(spec.bundles),
        "settings": mask_secrets(settings_obj.model_dump()),
    }
    logger.info(
        "config profile=%s bundles=%s config_hash=%s",
        name,
        ",".join(spec.bundles),
        config_hash(assembly),
    )
    return settings_obj


def get_settings() -> Settings:
    """返回缓存设置；未加载时按默认 profile（web）加载。"""
    if _settings is None:
        return load_settings()
    return _settings


def reset_settings() -> None:
    """清缓存并重置惰性代理（仅测试与显式重载使用）。"""
    global _settings, _active_profile, _active_spec, _active_patches
    _settings = None
    _active_profile = None
    _active_spec = None
    _active_patches = []
    from app.core.config import settings as _proxy

    reset = getattr(_proxy, "_reset", None)
    if callable(reset):
        reset()


def get_active_profile() -> str:
    """已加载的 profile 名；未加载时默认 ``web``（与 ``--profile`` 缺省一致）。"""
    return _active_profile or "web"


def get_active_entrypoint() -> str:
    """已加载 profile 的入口类型；未加载时默认 ``web``。"""
    return _active_spec.entrypoint if _active_spec is not None else "web"


def get_active_bundles() -> list[str]:
    """已加载 profile 的 bundle 列表；未加载时返回 web 的默认组合。"""
    if _active_spec is not None:
        return list(_active_spec.bundles)
    return ["core", "rag", "agent", "observability"]


__all__ = [
    "BACKEND_DIR",
    "CONFIG_DIR",
    "config_hash",
    "get_active_bundles",
    "get_active_entrypoint",
    "get_active_profile",
    "get_settings",
    "load_settings",
    "mask_secrets",
    "reset_settings",
]
