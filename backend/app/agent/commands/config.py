"""命令配置加载 —— 可选的 ``commands.json``（backend 根目录，env 可覆盖路径）。

与 ``app/agent/mcp/config.py`` 同一套仓库惯例：**配置存在 ≠ 主动启用**；
非法条目逐条 warning 跳过，**任何情况都不抛出**（本模块处于启动路径）。

配置形态::

    {
      "disabled": ["export"],                    // 全局禁用内置命令（按名/别名）
      "agents": {
        "session:<会话 id>": {
          "enable": ["export"],                  // 该 agent 作用域额外注册（遮蔽全局）
          "disable": ["help"]                    // 该作用域禁用全局命令（按名/别名）
        }
      }
    }

作用域键与运行期约定一致（``session:<id>``，见 ``agent_scope_for_session``）；
配置只能**引用已注册的代码 handler**（enable 的是内置命令），不执行配置里的
任意代码——这是与 MCP server 配置一致的信任边界。

优先级（评审 m4）：同一作用域内，``enable`` 注册的 scoped 命令优先于全局
（解析先查 scoped、列表按遮蔽去重）——即使同一配置的 ``disable`` 也列出了
该命令，``enable`` 仍生效（apply 先 enable 后 block，但 scoped 命中永远先于
全局）。``enable`` 不能凭配置发明新命令：引用未注册的命令名只留 warning。

变异点（tests/unit/test_user_commands.py）：
- 坏 JSON 抛异常（启动路径炸掉）→ bad-json 用例红；
- 非法条目静默跳过（无 warning）→ invalid-entry 用例红；
- 配置只加载不应用 → apply 用例红。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.commands.registry import CommandRegistry

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILENAME = "commands.json"
CONFIG_PATH_ENV = "DOCMIND_COMMANDS_CONFIG"
_MAX_SCOPE_KEY_LENGTH = 200


@dataclass
class AgentCommandScopeConfig:
    """一个 agent 作用域的配置（enable=额外注册；disable=屏蔽全局）。"""

    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)


@dataclass
class CommandsConfig:
    disabled: list[str] = field(default_factory=list)
    agents: dict[str, AgentCommandScopeConfig] = field(default_factory=dict)


def default_config_path() -> Path:
    """backend/commands.json（相对本文件定位，不依赖 cwd）。"""
    # config.py → commands/ → agent/ → app/ → backend/
    return Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME


def resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    from_env = os.environ.get(CONFIG_PATH_ENV)
    if from_env:
        return Path(from_env)
    return default_config_path()


def _string_list(raw: object, *, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("commands 配置 %s 应为数组，实际 %s，忽略", where, type(raw).__name__)
        return []
    values: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            values.append(item.strip().lstrip("/").lower())
        else:
            logger.warning("commands 配置 %s 含非法条目 %r，跳过", where, item)
    return values


def load_commands_config(path: str | Path | None = None) -> CommandsConfig:
    """加载并校验配置文件；缺失/损坏/非法条目 → 空配置 + warning，绝不抛出。"""
    resolved = resolve_config_path(path)
    if not resolved.is_file():
        logger.debug("commands 配置未提供（%s 不存在），仅注册内置命令", resolved)
        return CommandsConfig()

    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        # UnicodeDecodeError 是 UnicodeError（ValueError 子类），不在 OSError 下：
        # 非 UTF-8 文件同样按"配置不可用"降级，绝不抛出（评审 m5）。
        logger.warning("commands 配置读取失败（%s）：%s", resolved, exc)
        return CommandsConfig()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("commands 配置 JSON 解析失败（%s）：%s", resolved, exc)
        return CommandsConfig()

    if not isinstance(data, dict):
        logger.warning("commands 配置顶层应为对象，实际 %s，忽略", type(data).__name__)
        return CommandsConfig()

    config = CommandsConfig(disabled=_string_list(data.get("disabled"), where="disabled"))

    agents_raw = data.get("agents")
    if agents_raw is None:
        return config
    if not isinstance(agents_raw, dict):
        logger.warning("commands 配置 agents 应为对象，实际 %s，忽略", type(agents_raw).__name__)
        return config

    for scope_key, scope_raw in agents_raw.items():
        if (
            not isinstance(scope_key, str)
            or not scope_key.strip()
            or len(scope_key.strip()) > _MAX_SCOPE_KEY_LENGTH
        ):
            logger.warning("commands 配置存在非法 agent 作用域键 %r，跳过", scope_key)
            continue
        if not isinstance(scope_raw, dict):
            logger.warning("commands 配置 agents.%s 应为对象，跳过", scope_key)
            continue
        config.agents[scope_key.strip()] = AgentCommandScopeConfig(
            enable=_string_list(scope_raw.get("enable"), where=f"agents.{scope_key}.enable"),
            disable=_string_list(scope_raw.get("disable"), where=f"agents.{scope_key}.disable"),
        )
    return config


def apply_commands_config(registry: CommandRegistry, config: CommandsConfig) -> None:
    """把配置应用到注册表（全局禁用 → 注销；agent 作用域 → enable/block）。

    所有失败路径只 warning：配置是增强，不得让启动失败。
    """
    for name in config.disabled:
        if not registry.unregister(name, scope="global"):
            logger.warning("commands 配置 disabled 引用了未知命令 %r，忽略", name)

    for scope_key, scope_config in config.agents.items():
        for name in scope_config.enable:
            command = registry.get_global(name)
            if command is None:
                logger.warning(
                    "commands 配置 agents.%s.enable 引用了未知命令 %r，忽略", scope_key, name
                )
                continue
            try:
                registry.register(command, scope=scope_key)
            except Exception as exc:  # noqa: BLE001 — 配置冲突不得炸启动
                logger.warning(
                    "commands 配置 agents.%s.enable 注册 %r 失败：%s", scope_key, name, exc
                )
        for name in scope_config.disable:
            command = registry.get_global(name)
            keys = command.all_names if command is not None else (name,)
            for key in keys:
                try:
                    registry.block(key, scope=scope_key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "commands 配置 agents.%s.disable 应用 %r 失败：%s", scope_key, key, exc
                    )
