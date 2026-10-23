"""配置 schema 校验与合并/补丁原语（issue #479，Profiles/Bundles/Patch）。

本模块只做「数据层」的工作，不读文件、不碰环境变量：

- :class:`ConfigError`：所有配置错误的统一载体（``path`` 指向具体键）；
- :class:`ProfileSpec`：``config/profiles/<name>.yaml`` 的结构校验；
- :class:`PatchOp`：patch 语法校验（路径白名单 + op 语义）；
- :func:`deep_merge`：映射深合并、list/标量整体替换；
- :func:`apply_patches`：replace/insert/delete 的路径存在性语义。

读取顺序与文件定位在 :mod:`app.core.config.loader` 里，两者分开是为了让
合并/校验可以脱离真实配置文件单测。
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.config import Settings

# profile 名与 entrypoint 取值是同一个闭集；``base`` 是公共层，不能作为 profile。
VALID_PROFILES: tuple[str, ...] = ("web", "worker", "headless", "sdk", "acp")
VALID_BUNDLES: tuple[str, ...] = ("core", "rag", "agent", "observability")

_SECRET_SUFFIXES: tuple[str, ...] = ("_KEY", "_SECRET", "_PASSWORD", "_TOKEN")
_SECRET_PLACEHOLDER_PREFIX = "${"


class ConfigError(Exception):
    """配置错误：``path`` 是错误定位（如 ``settings.NOPE`` 或文件路径）。"""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def is_secret_field(name: str) -> bool:
    """字段名是否属于 secret 类别（``*_KEY/*_SECRET/*_PASSWORD/*_TOKEN``）。"""
    return name.endswith(_SECRET_SUFFIXES)


def is_plaintext_secret(value: Any) -> bool:
    """非空且不是 ``${...}`` 占位符的字符串会被视为明文 secret。

    空字符串 / ``None`` 不算 secret（大多数 secret 字段默认就是空串）；
    ``${VAR}`` 形式的占位符留给部署侧做环境变量插值，配置文件里允许出现。
    """
    return (
        isinstance(value, str) and bool(value) and not value.startswith(_SECRET_PLACEHOLDER_PREFIX)
    )


def validate_settings_map(settings: dict[str, Any], *, origin: str) -> None:
    """校验一层 ``settings:`` 映射：键必须是 Settings 字段，secret 不得明文。

    ``origin`` 只用于错误消息，便于定位是哪个文件写错了。
    """
    if not isinstance(settings, dict):
        raise ConfigError("settings", f"{origin}: settings 必须是映射（mapping）")
    for key, value in settings.items():
        if key not in Settings.model_fields:
            raise ConfigError(f"settings.{key}", f"未知设置字段（来源 {origin}）")
        if is_secret_field(key) and is_plaintext_secret(value):
            raise ConfigError(
                f"settings.{key}",
                f"{origin}: 配置文件禁止出现明文 secret（请改用环境变量或 ${{...}} 占位符）",
            )


class ProfileSpec(BaseModel):
    """``config/profiles/<name>.yaml`` 的结构：入口类型 + 有序 bundle + 覆盖项。"""

    model_config = ConfigDict(extra="forbid")

    entrypoint: Literal["web", "worker", "headless", "sdk", "acp"]
    bundles: list[str]
    settings: dict[str, Any] = {}

    @field_validator("bundles")
    @classmethod
    def _validate_bundles(cls, value: list[str]) -> list[str]:
        for bundle in value:
            if bundle not in VALID_BUNDLES:
                raise ConfigError(f"bundles.{bundle}", f"未知 bundle: {bundle}")
        if len(set(value)) != len(value):
            raise ConfigError("bundles", f"bundles 存在重复项: {value}")
        return value

    @model_validator(mode="after")
    def _validate_settings(self) -> ProfileSpec:
        validate_settings_map(self.settings, origin="profile")
        return self


def validate_profile_name(name: str) -> str:
    """校验 ``--profile`` 取值；``base`` 是公共层，单独给出可读错误。"""
    if name == "base":
        raise ConfigError(
            "profile.base", "base 是公共层（base.yaml 自动叠加），不能作为 profile 使用"
        )
    if name not in VALID_PROFILES:
        raise ConfigError(
            f"profile.{name}",
            f"未知 profile: {name}（可选: {', '.join(VALID_PROFILES)}）",
        )
    return name


def _validate_patch_path(path: str) -> None:
    if not path.startswith("settings."):
        raise ConfigError(path or "patch.path", "patch 路径必须以 settings. 开头")
    segments = path.split(".")
    if any(not segment for segment in segments):
        raise ConfigError(path, "patch 路径存在空段（如 settings. 或 settings..X）")
    if ".." in path:
        raise ConfigError(path, "patch 路径不得包含 ..")
    if "__" in path:
        raise ConfigError(path, "patch 路径不得包含 __")
    if path.startswith("/") or any(segment.startswith("/") for segment in segments):
        raise ConfigError(path, "patch 路径不得是绝对路径")


class PatchOp(BaseModel):
    """单条 patch：``{op, path, value}``；路径必须落在 ``settings.`` 之下。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal["replace", "insert", "delete"]
    path: str
    value: Any = None

    @model_validator(mode="after")
    def _validate(self) -> PatchOp:
        _validate_patch_path(self.path)
        leaf = self.path.split(".")[-1]
        if self.op != "delete" and is_secret_field(leaf) and is_plaintext_secret(self.value):
            raise ConfigError(
                self.path,
                "patch 禁止出现明文 secret（请改用环境变量或 ${...} 占位符）",
            )
        return self


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """映射深合并；list 与标量整体替换；返回新对象，不原地修改入参。"""
    merged: dict[str, Any] = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _apply_one(settings_dict: dict[str, Any], op: PatchOp) -> None:
    segments = op.path.split(".")[1:]  # 去掉 "settings"
    node: Any = settings_dict
    for segment in segments[:-1]:
        if not isinstance(node, dict) or segment not in node or not isinstance(node[segment], dict):
            raise ConfigError(op.path, f"{op.op} 的父路径不存在或不是映射: {op.path}")
        node = node[segment]
    leaf = segments[-1]
    if not isinstance(node, dict):
        raise ConfigError(op.path, f"{op.op} 的父路径不是映射: {op.path}")
    exists = leaf in node
    if op.op == "replace":
        if not exists:
            raise ConfigError(op.path, f"replace 要求路径已存在: {op.path}")
        node[leaf] = copy.deepcopy(op.value)
    elif op.op == "insert":
        if exists:
            raise ConfigError(op.path, f"insert 要求路径不存在: {op.path}")
        node[leaf] = copy.deepcopy(op.value)
    else:  # delete
        if not exists:
            raise ConfigError(op.path, f"delete 要求路径已存在: {op.path}")
        del node[leaf]


def apply_patches(settings_dict: dict[str, Any], ops: list[PatchOp]) -> dict[str, Any]:
    """按顺序应用 patch；返回新对象。路径语义不满足时抛 :class:`ConfigError`。"""
    patched = copy.deepcopy(settings_dict)
    for op in ops:
        _apply_one(patched, op)
    return patched
