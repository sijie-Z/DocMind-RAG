"""hooks.yaml 加载与校验（issue #474 §5.4）—— 启动期 fail-closed，字段路径可定位。

设计取舍：

- **严格校验、错误即抛**：``HOOKS_ENABLED=true`` 时配置是启动路径的一部分，
  非法条目必须在 lifespan 抛 ``HookConfigError`` 让进程退出（issue 明确要求
  fail-closed）——与 commands.json"坏配置降级不阻塞启动"不同，因为 hook 会
  改写/阻断 agent 执行，带病运行比不启动更危险。
- **模块白名单先于 import**：``HOOK_MODULE_PATTERN`` 只允许
  ``app.agent.hooks.*`` 的 dotted path，天然拒绝 ``..`` / 绝对路径 /
  ``os``/``subprocess`` 等任意 import（issue §5.4）。
- **HOOKS_ENABLED=false 零读取**：``initialize_from_settings`` 在关闭时直接返回
  None，连路径解析都不做（issue §3 隐含需求）。

变异点（tests/unit/test_hooks_config.py）：
- 放宽模块正则 → test_module_whitelist_rejects_* 红；
- 不校验 timeout/priority/policy/未知字段 → 对应用例红；
- 错误信息缺字段路径 → test_error_contains_field_path 红；
- 关闭时仍读文件 → test_disabled_never_touches_file 红。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.agent.hooks.models import (
    EVENTS,
    HOOK_MODULE_PATTERN,
    HOOK_NAME_PATTERN,
    HookSpec,
)

logger = logging.getLogger("docmind.hooks")

# backend/ 目录（本文件：backend/app/agent/hooks/loader.py）
BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = "config/hooks.yaml"

_ALLOWED_KEYS = {
    "name",
    "event",
    "module",
    "enabled",
    "priority",
    "timeout_seconds",
    "failure_policy",
}
_FAILURE_POLICIES = {"fail_open", "fail_closed"}
_MIN_TIMEOUT = 0.1
_MAX_TIMEOUT = 30.0


class HookConfigError(Exception):
    """hooks.yaml 非法（启动期 fail-closed；message 必须含字段路径）。"""


def resolve_config_path(path: str | Path | None = None) -> Path:
    """解析 hooks.yaml 路径：相对路径按 backend/ 解析（不依赖 cwd）。"""
    if path is None or str(path).strip() == "":
        path = DEFAULT_CONFIG_PATH
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = BACKEND_DIR / candidate
    return candidate


def _fail(field: str, message: str) -> HookConfigError:
    return HookConfigError(f"{field}: {message}")


def _parse_entry(idx: int, raw: Any) -> HookSpec:
    field = f"hooks[{idx}]"
    if not isinstance(raw, dict):
        raise _fail(field, f"条目必须是对象，实际 {type(raw).__name__}")

    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise _fail(f"{field}.{unknown[0]}", "未知字段（拼写错误？）")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _fail(f"{field}.name", "必填，且必须是非空字符串")
    name = name.strip()
    if not HOOK_NAME_PATTERN.match(name):
        raise _fail(
            f"{field}.name",
            "非法名称（要求 ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$）",
        )

    event = raw.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        raise _fail(f"{field}.event", f"非法事件 {event!r}（允许: {', '.join(EVENTS)}）")

    module = raw.get("module")
    if not isinstance(module, str) or not HOOK_MODULE_PATTERN.match(module):
        raise _fail(
            f"{field}.module",
            f"非法模块路径 {module!r}（必须以 app.agent.hooks. 开头且为合法 dotted path）",
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _fail(f"{field}.enabled", f"必须为布尔值，实际 {type(enabled).__name__}")

    priority = raw.get("priority", 100)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise _fail(f"{field}.priority", f"必须为整数，实际 {type(priority).__name__}")

    timeout = raw.get("timeout_seconds", 2.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise _fail(f"{field}.timeout_seconds", f"必须为数字，实际 {type(timeout).__name__}")
    if not (_MIN_TIMEOUT <= float(timeout) <= _MAX_TIMEOUT):
        raise _fail(
            f"{field}.timeout_seconds",
            f"越界 {timeout}（允许 {_MIN_TIMEOUT}–{_MAX_TIMEOUT} 秒）",
        )

    policy = raw.get("failure_policy", "fail_open")
    if policy not in _FAILURE_POLICIES:
        raise _fail(
            f"{field}.failure_policy",
            f"非法策略 {policy!r}（允许: {', '.join(sorted(_FAILURE_POLICIES))}）",
        )

    return HookSpec(
        name=name,
        event=event,
        module=module,
        enabled=enabled,
        priority=priority,
        timeout_seconds=float(timeout),
        failure_policy=policy,
    )


def load_hook_specs(path: str | Path) -> list[HookSpec]:
    """读取并严格校验 hooks.yaml；任何非法 → HookConfigError（含字段路径）。"""
    resolved = resolve_config_path(path)
    if not resolved.is_file():
        raise HookConfigError(f"hooks 配置不存在: {resolved}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HookConfigError(f"hooks 配置读取失败（{resolved}）：{exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HookConfigError(f"hooks 配置 YAML 解析失败（{resolved}）：{exc}") from exc

    if not isinstance(data, dict):
        raise HookConfigError(
            f"hooks 配置顶层必须是对象（含 hooks 键），实际 {type(data).__name__}"
        )
    if "hooks" not in data:
        raise HookConfigError("hooks: 缺少顶层 `hooks` 键")
    raw_hooks = data.get("hooks")
    if not isinstance(raw_hooks, list):
        raise HookConfigError(f"hooks: 必须是数组，实际 {type(raw_hooks).__name__}")

    specs: list[HookSpec] = []
    seen_names: dict[str, int] = {}
    for idx, raw in enumerate(raw_hooks):
        spec = _parse_entry(idx, raw)
        if spec.name in seen_names:
            raise _fail(
                f"hooks[{idx}].name",
                f"名称重复 {spec.name!r}（与 hooks[{seen_names[spec.name]}] 冲突）",
            )
        seen_names[spec.name] = idx
        specs.append(spec)
    return specs


async def initialize_from_settings(settings_obj: Any) -> Any:
    """启动期入口：仅当 ``HOOKS_ENABLED=true`` 时读取配置并启用总线。

    返回启用后的 HookBus；关闭时返回 None 且**不解析路径、不读文件**。
    """
    if not bool(getattr(settings_obj, "HOOKS_ENABLED", False)):
        return None
    from app.agent.hooks.registry import get_hook_bus

    path = getattr(settings_obj, "HOOKS_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    bus = get_hook_bus()
    await bus.load_from_config(str(path))
    bus.enabled = True
    return bus
