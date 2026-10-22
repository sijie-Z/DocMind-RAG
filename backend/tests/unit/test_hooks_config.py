"""Hooks 配置加载（issue #474 §5.4）—— YAML 校验、模块白名单、fail-closed 启动语义。

被测契约：

- ``hooks.yaml`` 的每个条目校验 name/event/module/priority/timeout_seconds/
  failure_policy/enabled；任何非法 → ``HookConfigError``，错误信息含**字段路径**
  （如 ``hooks[0].module``）——lifespan 据此 fail-closed 退出。
- module 必须匹配 ``app.agent.hooks.*`` dotted path：``os.system``、``os``、
  ``subprocess``、``..``、绝对路径一律拒绝（禁止任意 import）。
- timeout 允许 0.1–30，越界拒绝；priority 必须整数；failure_policy 二选一。
- ``HOOKS_ENABLED=false`` 时初始化不读文件（由 initialize_from_settings 保证）；
  启用但文件缺失/损坏 → HookConfigError（fail-closed，不静默空转）。
- 单个 hook 的 ``enabled: false`` 只跳过注册，不导入模块。

变异点：
- 白名单放宽到任意 module → test_module_whitelist_rejects_* 红；
- 超时/策略不校验 → 对应用例红；
- 错误信息不带字段路径 → test_error_contains_field_path 红；
- 禁用时仍读文件 → test_disabled_never_touches_file 红；
- enabled=false 的坏模块仍被导入 → test_disabled_hook_not_imported 红。
"""

from __future__ import annotations

import sys
import types

import pytest

from app.agent.hooks import (
    HookBus,
    HookConfigError,
    initialize_from_settings,
    load_hook_specs,
    resolve_config_path,
)


def _write(tmp_path, text: str):
    path = tmp_path / "hooks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
hooks:
  - name: audit_tool_calls
    event: tools/pre_execute
    module: app.agent.hooks.builtin.audit_tool_calls
    enabled: true
    priority: 100
    timeout_seconds: 2.0
    failure_policy: fail_open
"""


def _install_fake_module(monkeypatch, name: str, handler) -> None:
    mod = types.ModuleType(name)
    mod.handler = handler
    monkeypatch.setitem(sys.modules, name, mod)


# ── 合法配置 ────────────────────────────────────────────────────────────────


def test_valid_config_parses_all_fields(tmp_path):
    specs = load_hook_specs(_write(tmp_path, VALID))
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "audit_tool_calls"
    assert spec.event == "tools/pre_execute"
    assert spec.module == "app.agent.hooks.builtin.audit_tool_calls"
    assert spec.priority == 100
    assert spec.timeout_seconds == 2.0
    assert spec.failure_policy == "fail_open"
    assert spec.enabled is True


def test_defaults_applied(tmp_path):
    specs = load_hook_specs(
        _write(
            tmp_path,
            """
hooks:
  - name: minimal
    event: agent/post_step
    module: app.agent.hooks.builtin.audit_tool_calls
""",
        )
    )
    spec = specs[0]
    assert spec.priority == 100
    assert spec.timeout_seconds == 2.0
    assert spec.failure_policy == "fail_open"
    assert spec.enabled is True


def test_empty_hooks_list_is_valid(tmp_path):
    assert load_hook_specs(_write(tmp_path, "hooks: []\n")) == []


# ── 非法配置：字段路径必须可定位 ─────────────────────────────────────────────


def test_error_contains_field_path(tmp_path):
    path = _write(
        tmp_path,
        """
hooks:
  - name: bad_timeout
    event: tools/pre_execute
    module: app.agent.hooks.builtin.audit_tool_calls
    timeout_seconds: 99
""",
    )
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert "hooks[0].timeout_seconds" in str(excinfo.value)


@pytest.mark.parametrize(
    "module",
    [
        "os.system",
        "os",
        "subprocess",
        "subprocess.Popen",
        "app.agent.hooks...builtin.x",
        "/abs/path/module",
        "C:\\evil\\module",
        "app.agent.hooks.builtin.evil; rm -rf /",
    ],
)
def test_module_whitelist_rejects(tmp_path, module):
    import yaml

    path = _write(
        tmp_path,
        yaml.safe_dump(
            {
                "hooks": [
                    {
                        "name": "evil",
                        "event": "tools/pre_execute",
                        "module": module,
                    }
                ]
            },
            allow_unicode=True,
        ),
    )
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert "hooks[0].module" in str(excinfo.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "llm/request"),
        ("event", ""),
        ("failure_policy", "fail_everywhere"),
        ("timeout_seconds", 0.05),
        ("timeout_seconds", 30.5),
        ("timeout_seconds", "2"),
        ("priority", "high"),
        ("priority", 1.5),
        ("priority", True),
        ("enabled", "yes"),
    ],
)
def test_field_validation_rejects(tmp_path, field, value):
    entry = {
        "name": "x",
        "event": "tools/pre_execute",
        "module": "app.agent.hooks.builtin.audit_tool_calls",
        field: value,
    }
    # 用简单 YAML 文本构造（保留字符串引号语义）
    body = "\n".join(
        f"    {k}: {v!r}" if isinstance(v, str) else f"    {k}: {v}"
        for k, v in entry.items()
        if k != "name"
    )
    path = _write(tmp_path, "hooks:\n  - name: x\n" + body + "\n")
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert f"hooks[0].{field}" in str(excinfo.value)


def test_missing_required_fields_rejected(tmp_path):
    path = _write(tmp_path, "hooks:\n  - event: tools/pre_execute\n")
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert "hooks[0].name" in str(excinfo.value)


def test_unknown_field_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
hooks:
  - name: x
    event: tools/pre_execute
    module: app.agent.hooks.builtin.audit_tool_calls
    timeout: 2
""",
    )
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert "hooks[0].timeout" in str(excinfo.value)


def test_duplicate_names_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
hooks:
  - name: dup
    event: tools/pre_execute
    module: app.agent.hooks.builtin.audit_tool_calls
  - name: dup
    event: agent/pre_step
    module: app.agent.hooks.builtin.audit_tool_calls
""",
    )
    with pytest.raises(HookConfigError) as excinfo:
        load_hook_specs(path)
    assert "hooks[1].name" in str(excinfo.value)


def test_malformed_yaml_rejected(tmp_path):
    with pytest.raises(HookConfigError):
        load_hook_specs(_write(tmp_path, "hooks: [unclosed\n"))


def test_top_level_must_be_mapping(tmp_path):
    with pytest.raises(HookConfigError):
        load_hook_specs(_write(tmp_path, "- just\n- a\n- list\n"))


# ── 加载与注册 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_from_config_imports_and_registers(tmp_path, monkeypatch):
    async def handler(ctx, nxt):
        return await nxt(ctx.payload)

    _install_fake_module(monkeypatch, "app.agent.hooks.builtin.fake_ok", handler)
    path = _write(
        tmp_path,
        """
hooks:
  - name: fake_ok
    event: tools/pre_execute
    module: app.agent.hooks.builtin.fake_ok
""",
    )
    bus = HookBus()
    await bus.load_from_config(str(path))
    assert bus.handler_count == 1


@pytest.mark.asyncio
async def test_missing_handler_attr_rejected(tmp_path, monkeypatch):
    mod = types.ModuleType("app.agent.hooks.builtin.no_handler")
    monkeypatch.setitem(sys.modules, "app.agent.hooks.builtin.no_handler", mod)
    path = _write(
        tmp_path,
        """
hooks:
  - name: no_handler
    event: tools/pre_execute
    module: app.agent.hooks.builtin.no_handler
""",
    )
    bus = HookBus()
    with pytest.raises(HookConfigError) as excinfo:
        await bus.load_from_config(str(path))
    assert "hooks[0].module" in str(excinfo.value)


@pytest.mark.asyncio
async def test_missing_file_fails_closed_when_loaded(tmp_path):
    bus = HookBus()
    with pytest.raises(HookConfigError):
        await bus.load_from_config(str(tmp_path / "nope.yaml"))


@pytest.mark.asyncio
async def test_disabled_hook_not_imported(tmp_path, monkeypatch):
    # 模块不存在但 enabled: false —— 只校验格式、不导入
    path = _write(
        tmp_path,
        """
hooks:
  - name: dormant
    event: tools/pre_execute
    module: app.agent.hooks.builtin.does_not_exist_zzz
    enabled: false
""",
    )
    bus = HookBus()
    await bus.load_from_config(str(path))
    assert bus.handler_count == 0


# ── 初始化开关（HOOKS_ENABLED=false 零读取）─────────────────────────────────


class _Settings:
    def __init__(self, enabled: bool, path: str = ""):
        self.HOOKS_ENABLED = enabled
        self.HOOKS_CONFIG_PATH = path


@pytest.mark.asyncio
async def test_disabled_never_touches_file(monkeypatch):
    import app.agent.hooks.loader as loader_mod

    def _explode(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("HOOKS_ENABLED=false 时不得读取配置文件")

    # 打在 loader 的真实全局上（initialize_from_settings 定义在 loader 模块内），
    # 而不是包级 re-export —— 否则检查的只是"包名没被用到"，证明力不足。
    monkeypatch.setattr(loader_mod, "load_hook_specs", _explode)
    monkeypatch.setattr(loader_mod, "resolve_config_path", _explode)
    bus = await initialize_from_settings(_Settings(False, "definitely/not/read.yaml"))
    assert bus is None


@pytest.mark.asyncio
async def test_enabled_loads_and_enables(tmp_path, monkeypatch):
    async def handler(ctx, nxt):
        return await nxt(ctx.payload)

    _install_fake_module(monkeypatch, "app.agent.hooks.builtin.fake_init", handler)
    path = _write(
        tmp_path,
        """
hooks:
  - name: fake_init
    event: agent/pre_step
    module: app.agent.hooks.builtin.fake_init
""",
    )
    from app.agent.hooks import get_hook_bus, set_hook_bus

    original = get_hook_bus()
    set_hook_bus(HookBus(enabled=False))
    try:
        bus = await initialize_from_settings(_Settings(True, str(path)))
        assert bus is not None and bus.enabled is True
        assert bus.handler_count == 1
    finally:
        # 单例是进程级的：本用例启用后必须还原，避免污染后续用例的"默认关闭"语义。
        set_hook_bus(original)


def test_resolve_config_path_relative_to_backend(tmp_path):
    resolved = resolve_config_path("config/hooks.yaml")
    assert resolved.is_absolute()
    assert resolved.parts[-2:] == ("config", "hooks.yaml")
    # 绝对路径原样（用平台绝对路径，避免 Windows 下 "/tmp" 的相对歧义）
    absolute = tmp_path / "custom.yaml"
    assert resolve_config_path(str(absolute)) == absolute


def test_shipped_hooks_yaml_is_valid():
    """仓库自带的 backend/config/hooks.yaml 必须能被解析（默认不启用也要求格式正确）。"""
    shipped = resolve_config_path("config/hooks.yaml")
    assert shipped.is_file(), "backend/config/hooks.yaml 必须随仓库提供示例"
    specs = load_hook_specs(shipped)
    assert specs and all(s.event for s in specs)
