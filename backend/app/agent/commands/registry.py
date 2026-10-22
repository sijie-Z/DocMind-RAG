"""命令注册表 —— 全局作用域 + agent（会话）作用域，同层重名报错、子作用域遮蔽全局。

语义（issue #475）：
- **作用域**：``GLOBAL_SCOPE = "global"`` 之外的一切键都是 agent 作用域
  （本仓库约定 ``session:<会话 id>``，见 ``agent_scope_for_session``）。
- **同层重名**：同一作用域内 canonical name 与 aliases 共用一个命名空间，
  任意重名（含"名字撞别人别名"）→ ``DuplicateCommandError``。**不静默覆盖**。
- **遮蔽**：查找先查 agent 作用域、再落全局；agent 命令的 name **或任一别名**
  与全局命令的任一键冲突时，该全局命令在**这个作用域内**整体不可达、列表
  中也不出现（否则会出现"列表里有、执行却打到另一个命令"的错位）。
- **block**：配置可对某个 agent 作用域禁用全局命令（按名称或别名），解析与
  列表同时生效；blocked 不影响其它作用域，也不影响 agent 作用域自己的注册。
- **权限**：``list_commands(is_admin=False)`` 过滤 ``permission="admin"`` 的
  命令——隐藏与执行校验同源，绝不只做菜单层隐藏。

变异点（tests/unit/test_user_commands.py）：
- 同层重名不报错 → duplicate 用例红；
- resolve 不先查 agent 作用域 → shadow 用例红；
- 遮蔽只看 canonical name、不看别名 → alias-shadow 用例红；
- list 不过滤 admin / 不按遮蔽去重 → 对应用例红。
"""

from __future__ import annotations

import re

from app.agent.commands.types import Command

GLOBAL_SCOPE = "global"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class CommandRegistrationError(ValueError):
    """命令描述符非法（名字/别名不符合命名规范）。"""


class DuplicateCommandError(CommandRegistrationError):
    """同一作用域内命令名 / 别名冲突。"""


def agent_scope_for_session(session_id: str) -> str:
    """会话的 agent 作用域键（与 agent loop 的 ``session:<id>`` 命名一致）。"""
    return f"session:{session_id}"


def normalize_scope(scope: str | None) -> str:
    """空作用域归一为 global；其余按原样（大小写敏感）作为作用域键。"""
    cleaned = (scope or "").strip()
    return cleaned or GLOBAL_SCOPE


class CommandRegistry:
    """进程内命令注册表（单例见 ``app.agent.commands.command_registry``）。

    线程/协程安全说明：注册发生在启动路径（单线程 import 期）与显式测试中；
    热路径只读。运行期动态注册（SDK/ACP 扩展）需调用方自行串行化——本类不加锁。
    """

    def __init__(self) -> None:
        self._scopes: dict[str, dict[str, Command]] = {GLOBAL_SCOPE: {}}
        # scope → 被禁用的全局命令键（小写 name / alias）
        self._blocked: dict[str, set[str]] = {}

    # ── 注册 / 注销 ──────────────────────────────────────────────────────────

    def register(self, command: Command, *, scope: str = GLOBAL_SCOPE) -> None:
        scope = normalize_scope(scope)
        keys = [name.strip().lower() for name in command.all_names]
        for key in keys:
            if not _NAME_RE.match(key):
                raise CommandRegistrationError(
                    f"非法命令名/别名 {key!r}（要求 ^[a-z][a-z0-9_-]{{0,31}}$）"
                )
        if len(set(keys)) != len(keys):
            raise CommandRegistrationError(f"命令 {command.name!r} 的 aliases 与 name 重复")

        existing = self._index(scope)
        collisions = sorted(key for key in keys if key in existing)
        if collisions:
            raise DuplicateCommandError(
                f"命令名或别名冲突：{', '.join(collisions)}（scope={scope}）"
            )
        self._scopes.setdefault(scope, {})[command.name] = command

    def unregister(self, name: str, *, scope: str = GLOBAL_SCOPE) -> bool:
        """按 name 或 alias 注销（同层唯一定位）；返回是否命中。"""
        scope = normalize_scope(scope)
        command = self._index(scope).get(name.strip().lower())
        if command is None:
            return False
        self._scopes.get(scope, {}).pop(command.name, None)
        return True

    def block(self, name: str, *, scope: str) -> None:
        """在指定 agent 作用域禁用某个全局命令键（名称或别名）。"""
        scope = normalize_scope(scope)
        if scope == GLOBAL_SCOPE:
            raise CommandRegistrationError("global 作用域不能 block；请用 unregister")
        key = name.strip().lower()
        if not _NAME_RE.match(key):
            raise CommandRegistrationError(f"非法命令名 {key!r}")
        self._blocked.setdefault(scope, set()).add(key)

    def unblock(self, name: str, *, scope: str) -> None:
        self._blocked.get(normalize_scope(scope), set()).discard(name.strip().lower())

    # ── 查找 / 列表 ──────────────────────────────────────────────────────────

    def resolve(self, name: str, *, agent_scope: str | None = None) -> tuple[Command | None, str]:
        """按 name / alias 解析命令，返回 (command, 命中作用域)。

        未命中返回 ``(None, GLOBAL_SCOPE)``——调用方据此返回显式未知命令错误，
        绝不回落到模型。

        遮蔽是**按整条命令**生效的：agent 作用域的命令键（name 或任一 alias）
        与全局命令的任一键冲突时，即使调用方请求的是全局命令的另一个键，也不得
        解析到它（否则会出现"列表隐藏、执行仍可达"的错位）。block 同理。
        """
        key = (name or "").strip().lower()
        scope = normalize_scope(agent_scope)
        if scope != GLOBAL_SCOPE:
            scoped = self._index(scope).get(key)
            if scoped is not None:
                return scoped, scope
            shadowed = set(self._index(scope)) | self._blocked.get(scope, set())
            if key in shadowed:
                return None, GLOBAL_SCOPE
        else:
            shadowed = set()
        command = self._index(GLOBAL_SCOPE).get(key)
        if command is not None:
            if any(alias.lower() in shadowed for alias in command.all_names):
                return None, GLOBAL_SCOPE
            return command, GLOBAL_SCOPE
        return None, GLOBAL_SCOPE

    def get_global(self, name: str) -> Command | None:
        return self.resolve(name, agent_scope=None)[0]

    def list_commands(
        self, *, agent_scope: str | None = None, is_admin: bool = False
    ) -> list[Command]:
        """当前作用域可见命令（遮蔽去重 + 权限过滤），按 canonical name 排序。"""
        scope = normalize_scope(agent_scope)
        selected: dict[str, Command] = {}
        shadow_keys: set[str] = set()

        if scope != GLOBAL_SCOPE:
            for command in self._scopes.get(scope, {}).values():
                selected[command.name] = command
                shadow_keys.update(name.lower() for name in command.all_names)
            shadow_keys |= self._blocked.get(scope, set())

        for command in self._scopes.get(GLOBAL_SCOPE, {}).values():
            if command.name in selected:
                continue
            if any(name.lower() in shadow_keys for name in command.all_names):
                continue
            selected[command.name] = command

        visible = [
            command for command in selected.values() if is_admin or command.permission == "user"
        ]
        return sorted(visible, key=lambda command: command.name)

    def scopes(self) -> list[str]:
        """已注册的作用域键（含 global；配置/诊断用）。"""
        return sorted(set(self._scopes) | set(self._blocked))

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _index(self, scope: str) -> dict[str, Command]:
        """scope 内的 name/alias → command 索引（注册时已保证键唯一）。"""
        return {
            name.lower(): command
            for command in self._scopes.get(scope, {}).values()
            for name in command.all_names
        }
