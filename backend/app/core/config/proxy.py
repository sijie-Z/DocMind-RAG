"""模块级 ``settings`` 的惰性代理（issue #479）。

任何模块 import ``app.core.config.settings`` 时都不触发配置加载；首次**属性
访问**才调用 :func:`app.core.config.loader.get_settings`。这样 CLI 可以在
import 任何业务模块之前完成 ``--profile`` / ``--patch`` 解析，避免参数被
import 顺序吞掉。

读写语义：

- 读：``__getattr__`` 转发到真实 ``Settings``；
- 写：``__setattr__`` / ``__delattr__`` 转发到真实 ``Settings``——现有测试
  大量使用 ``monkeypatch.setattr(settings, "X", v)``，必须继续可用；
- ``_reset()``：清空内部目标，供 ``reset_settings()`` 使用。

不实现 ``__getattribute__``：代理自身的属性（``_target``）与 dunder 协议走
正常查找路径，不会被转发逻辑吞掉。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from app.core.config import Settings


class SettingsProxy:
    """惰性单例代理：第一次属性访问时才加载真实 ``Settings``。"""

    def __init__(self) -> None:
        object.__setattr__(self, "_target", None)

    def _resolve(self) -> Settings:
        target = object.__getattribute__(self, "_target")
        if target is None:
            # 延迟导入避免 config 包初始化期的循环依赖。
            from app.core.config.loader import get_settings

            target = get_settings()
            object.__setattr__(self, "_target", target)
        return target

    def _reset(self) -> None:
        object.__setattr__(self, "_target", None)

    @property
    def __dict__(self) -> dict[str, Any]:  # type: ignore[override]
        """转发真实 ``Settings.__dict__``。

        pytest 9.2（#10644）对非类目标、且无数据描述符的属性，改为从
        ``target.__dict__`` 快照旧值；快照不到就 ``delattr`` 回滚。代理的
        ``__dict__`` 原本只有 ``_target``，导致 ``monkeypatch.setattr(settings,
        "X", v)`` 的 undo 把真实字段从 pydantic 实例上删掉，后续所有读取
        级联失败。转发后 pytest 能看到真实旧值并用 ``setattr`` 恢复。
        """
        return self._resolve().__dict__

    def __getattr__(self, name: str) -> Any:
        # 只在正常查找失败时调用；dunder 查询（copy/pickle 等）直接按未定义处理。
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_target":
            object.__setattr__(self, name, value)
            return
        setattr(self._resolve(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._resolve(), name)
