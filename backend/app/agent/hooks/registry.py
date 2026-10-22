"""HookBus —— 注册、配置加载、waterfall/serial 执行（issue #474 §5.2/§5.5/§5.6）。

语义（与 issue 对齐）：

- **waterfall**（默认）：handler 签名 ``(ctx, next)``。``await next(payload)``
  继续链路并把（可能改写过的）payload 传给后续 hook；不调用 next 即短路，
  自己的返回值就是最终决策；返回 ``None`` 视为 allow 且 payload 保持当前值。
- **serial**（``agent/post_step``、``agent/turn_end``）：next 传 ``None``，
  顺序 await，返回值忽略——观察事件不阻断主流程。
- **顺序**：低 ``priority`` 先执行；同优先级按注册顺序（稳定排序）。
- **超时**：每个 hook 独立 ``timeout_seconds``；超时/异常/非法决策都产出
  ``HookError``（日志 + 指标 + SSE），按 ``failure_policy`` 收敛：
  fail_open 继续链路，fail_closed 返回 deny。
- **递归防护**：``_hook_depth`` contextvar，深度 >= 1 时的再次 ``emit`` 直接
  allow——hook 内触发的工具调用不会再触发 ``tools/pre_execute``（最大深度 1）。
- **默认关闭**：``enabled=False`` 时所有 emit 直接 allow、handler 零调用。

变异点（tests/unit/test_hooks_registry.py）：
- 排序键漏 priority/注册序 → order 用例红；
- 不跟踪 next 调用/短路 → short_circuit 与 timeout 用例红；
- 异常不外发 HookError → 各 failure 用例红；
- 递归防护缺失 → recursion 用例红。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from app.agent.hooks.models import (
    EVENTS,
    SERIAL_EVENTS,
    TOOLS_PRE_EXECUTE,
    HookContext,
    HookDecision,
    HookError,
    HookSpec,
    redact_arguments,
)
from app.core.prometheus import HOOK_DURATION, HOOK_EXECUTIONS

logger = logging.getLogger("docmind.hooks")

Next = Callable[[dict[str, Any]], Awaitable[HookDecision]]
HookHandler = Callable[[HookContext, Next | None], Awaitable[HookDecision | None]]

_MAX_LOG_ARGS_CHARS = 500

# 递归防护：hook 执行期间深度置 1；此时再次 emit 直接 allow（不再触发任何 hook）。
_hook_depth: ContextVar[int] = ContextVar("docmind_hook_depth", default=0)


def hook_depth_active() -> bool:
    """当前是否处于 hook 执行内部（供测试/诊断）。"""
    return _hook_depth.get() > 0


def _log_arguments(ctx: HookContext) -> str:
    """日志用参数摘要：脱敏 + 截断；不含 messages 等大字段。"""
    payload = ctx.payload if isinstance(ctx.payload, dict) else {}
    arguments = payload.get("arguments")
    if arguments is None:
        # 非工具事件只保留少量结构化字段，绝不把 messages/result 正文写进日志。
        arguments = {
            key: payload[key]
            for key in ("tool_name", "step_index", "status", "reason", "query")
            if key in payload
        }
    try:
        text = json.dumps(redact_arguments(arguments), ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover — 不可序列化兜底
        text = "<unserializable>"
    return text[:_MAX_LOG_ARGS_CHARS]


class HookBus:
    """进程内 hook 总线（线程/协程安全说明：注册发生在启动路径与测试；
    运行期只读 handler 列表，不加锁——与 commands registry 同款约定）。"""

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._handlers: dict[str, list[tuple[HookSpec, HookHandler]]] = {}
        self._registration_seq = 0

    # ── 注册 ────────────────────────────────────────────────────────────────

    def register(self, spec: HookSpec, handler: HookHandler) -> None:
        """注册一个 hook；同事件内按 (priority, 注册顺序) 稳定排序。"""
        if not callable(handler):
            raise TypeError(f"hook {spec.name} 的 handler 不可调用")
        if spec.event not in EVENTS:
            raise ValueError(f"hook {spec.name} 的事件非法: {spec.event!r}（允许: {EVENTS}）")
        for existing, _ in self._handlers.get(spec.event, []):
            if existing.name == spec.name:
                raise ValueError(f"hook 名称重复: {spec.name}（event={spec.event}）")
        self._registration_seq += 1
        stored = spec.model_copy(update={"order": self._registration_seq})
        handlers = self._handlers.setdefault(stored.event, [])
        handlers.append((stored, handler))
        handlers.sort(key=lambda item: (item[0].priority, item[0].order))

    @property
    def handler_count(self) -> int:
        return sum(len(items) for items in self._handlers.values())

    def clear(self) -> None:
        self._handlers.clear()
        self._registration_seq = 0

    # ── 配置加载 ────────────────────────────────────────────────────────────

    async def load_from_config(self, path: str) -> None:
        """按 hooks.yaml 加载并注册。任何非法配置抛 ``HookConfigError``（fail-closed）。"""
        # 延迟导入：loader 只被启动路径使用，避免热路径 import yaml。
        from app.agent.hooks.loader import HookConfigError, load_hook_specs

        specs = load_hook_specs(path)
        for idx, spec in enumerate(specs):
            if not spec.enabled:
                # enabled: false 只跳过注册，**不导入模块**（禁用项不引入副作用）。
                continue
            try:
                module = importlib.import_module(spec.module)
            except Exception as exc:  # noqa: BLE001 — 启动期统一转配置错误
                raise HookConfigError(
                    f"hooks[{idx}].module 导入失败（{spec.module}）：{type(exc).__name__}: {exc}"
                ) from exc
            handler = getattr(module, "handler", None)
            if not callable(handler):
                raise HookConfigError(
                    f"hooks[{idx}].module（{spec.module}）必须导出可调用的 `handler(ctx, next)`"
                )
            try:
                self.register(spec, handler)
            except ValueError as exc:
                raise HookConfigError(f"hooks[{idx}].name：{exc}") from exc

    # ── 执行 ────────────────────────────────────────────────────────────────

    async def emit(self, event: str, ctx: HookContext) -> tuple[HookDecision, list[HookError]]:
        """执行某事件的全部 hook，返回 ``(最终决策, 失败记录列表)``。

        返回二元组（issue §5.7）而非 §5.2 的单一 HookDecision：executor/loop
        需要把每个 HookError 转成 ``hook_error`` SSE 事件（失败不得静默）。
        """
        errors: list[HookError] = []
        if event not in EVENTS:
            return HookDecision(), errors
        handlers = self._handlers.get(event) or []
        if not self.enabled or not handlers:
            return HookDecision(), errors
        if _hook_depth.get() >= 1:
            # 递归防护：hook 内部触发的任何事件都不再执行 hook（最大深度 1）。
            return HookDecision(action="allow", payload=dict(ctx.payload)), errors

        token = _hook_depth.set(_hook_depth.get() + 1)
        try:
            if event in SERIAL_EVENTS:
                return await self._emit_serial(event, ctx, handlers, errors), errors
            return (
                await self._emit_waterfall(event, ctx, handlers, 0, ctx.payload, errors),
                errors,
            )
        finally:
            _hook_depth.reset(token)

    async def _emit_waterfall(
        self,
        event: str,
        ctx: HookContext,
        handlers: list[tuple[HookSpec, HookHandler]],
        index: int,
        payload: dict[str, Any],
        errors: list[HookError],
    ) -> HookDecision:
        if index >= len(handlers):
            return HookDecision(action="allow", payload=payload)

        spec, handler = handlers[index]
        state: dict[str, Any] = {"called_next": False, "next_result": None}

        async def next_call(next_payload: dict[str, Any]) -> HookDecision:
            if not isinstance(next_payload, dict):
                raise TypeError("next(payload) 的 payload 必须是 dict")
            # 下游 handler 通过 ctx 读取当前 payload（签名只有 (ctx, next)）：
            # next(payload) 的语义 = 用新 payload 继续链路。
            ctx.payload = next_payload
            # 先置位再 await：若外层 hook 在本调用在途时超时，下游已经启动，
            # 失败收敛不得再次调用 next（否则下游副作用重复执行）。
            state["called_next"] = True
            result = await self._emit_waterfall(
                event, ctx, handlers, index + 1, next_payload, errors
            )
            state["next_result"] = result
            return result

        start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                handler(ctx, next_call), timeout=float(spec.timeout_seconds)
            )
        except TimeoutError:
            return await self._handle_failure(
                spec,
                event,
                ctx,
                errors,
                outcome="timeout",
                error_type="timeout",
                message=(
                    f"hook {spec.name} 执行超时（{spec.timeout_seconds}s），"
                    f"已按 {spec.failure_policy} 处理"
                ),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                payload=payload,
                state=state,
                next_call=next_call,
            )
        except Exception as exc:  # noqa: BLE001 — 单 hook 故障必须隔离
            # 只暴露异常类型：异常文本可能内嵌 hook 入参（密钥等），绝不进入
            # SSE content / 日志 message；详细信息由 hook 自己负责记录。
            return await self._handle_failure(
                spec,
                event,
                ctx,
                errors,
                outcome="error",
                error_type=type(exc).__name__,
                message=(
                    f"hook {spec.name} 执行失败（{type(exc).__name__}），"
                    f"已按 {spec.failure_policy} 处理"
                ),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                payload=payload,
                state=state,
                next_call=next_call,
            )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        decision, invalid_reason = self._normalize(event, spec, raw, payload, state)
        if invalid_reason is not None:
            return await self._handle_failure(
                spec,
                event,
                ctx,
                errors,
                outcome="invalid_decision",
                error_type="invalid_decision",
                message=invalid_reason,
                elapsed_ms=elapsed_ms,
                payload=payload,
                state=state,
                next_call=next_call,
            )

        self._observe(spec, event, decision.action, elapsed_ms, ctx)
        return decision

    def _normalize(
        self,
        event: str,
        spec: HookSpec,
        raw: HookDecision | None,
        payload: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[HookDecision | None, str | None]:
        """把 handler 返回值规整为 HookDecision；非法返回给出原因。"""
        if raw is None:
            if state["called_next"] and state["next_result"] is not None:
                return state["next_result"], None
            return HookDecision(action="allow", payload=payload), None
        if not isinstance(raw, HookDecision):
            return None, f"hook {spec.name} 返回类型非法: {type(raw).__name__}"
        if raw.action == "modify" and not isinstance(raw.payload, dict):
            return None, f"hook {spec.name} 的 modify 决策必须携带 dict payload"
        if raw.action == "ask" and event != TOOLS_PRE_EXECUTE:
            return None, f"hook {spec.name} 在 {event} 返回了仅 tools/pre_execute 支持的 ask"
        if raw.action == "allow" and raw.payload is None:
            return raw.model_copy(update={"payload": payload}), None
        return raw, None

    async def _handle_failure(
        self,
        spec: HookSpec,
        event: str,
        ctx: HookContext,
        errors: list[HookError],
        *,
        outcome: str,
        error_type: str,
        message: str,
        elapsed_ms: int,
        payload: dict[str, Any],
        state: dict[str, Any],
        next_call: Callable[[dict[str, Any]], Awaitable[HookDecision]],
    ) -> HookDecision:
        """失败收敛：记录 HookError + 指标/日志；fail_open 继续，fail_closed deny。"""
        error = HookError(
            hook=spec.name,
            event=event,
            action=spec.failure_policy,
            outcome=outcome,
            error_type=error_type,
            message=message,
            elapsed_ms=elapsed_ms,
        )
        errors.append(error)
        HOOK_EXECUTIONS.labels(event=event, hook=spec.name, outcome=outcome).inc()
        HOOK_DURATION.labels(event=event, hook=spec.name).observe(elapsed_ms / 1000.0)
        logger.warning(
            "hook failure event=%s hook=%s action=%s outcome=%s elapsed_ms=%d "
            "error_type=%s message=%s arguments=%s",
            event,
            spec.name,
            spec.failure_policy,
            outcome,
            elapsed_ms,
            error_type,
            message,
            _log_arguments(ctx),
        )
        if spec.failure_policy == "fail_closed":
            return HookDecision(action="deny", reason=f"hook {spec.name} {message}（fail_closed）")
        if state["called_next"]:
            # next 已经启动过：绝不二次执行下游链路（重复副作用）。
            # - next 已正常返回：沿用其最终决策；
            # - next 在途时被外层超时取消：没有可恢复的结果，按 allow 收敛
            #   （失败已记录为 HookError，不静默）。
            if state["next_result"] is not None:
                return state["next_result"]
            return HookDecision(action="allow", payload=payload)
        # fail_open：继续链路（由下一个 hook 接力；没有下一个则 allow）。
        return await next_call(payload)

    async def _emit_serial(
        self,
        event: str,
        ctx: HookContext,
        handlers: list[tuple[HookSpec, HookHandler]],
        errors: list[HookError],
    ) -> HookDecision:
        for spec, handler in handlers:
            start = time.perf_counter()
            try:
                await asyncio.wait_for(handler(ctx, None), timeout=float(spec.timeout_seconds))
            except TimeoutError:
                error = HookError(
                    hook=spec.name,
                    event=event,
                    action=spec.failure_policy,
                    outcome="timeout",
                    error_type="timeout",
                    message=(
                        f"hook {spec.name} 执行超时（{spec.timeout_seconds}s），"
                        f"已按 {spec.failure_policy} 处理"
                    ),
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                )
                self._record_serial_failure(spec, event, ctx, errors, error)
                if spec.failure_policy == "fail_closed":
                    return HookDecision(
                        action="deny", reason=f"hook {spec.name} 超时（fail_closed）"
                    )
                continue
            except Exception as exc:  # noqa: BLE001
                # 只暴露异常类型（异常文本可能内嵌入参/密钥，不进 SSE/日志 message）。
                error = HookError(
                    hook=spec.name,
                    event=event,
                    action=spec.failure_policy,
                    outcome="error",
                    error_type=type(exc).__name__,
                    message=(
                        f"hook {spec.name} 执行失败（{type(exc).__name__}），"
                        f"已按 {spec.failure_policy} 处理"
                    ),
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                )
                self._record_serial_failure(spec, event, ctx, errors, error)
                if spec.failure_policy == "fail_closed":
                    return HookDecision(
                        action="deny", reason=f"hook {spec.name} 失败（fail_closed）"
                    )
                continue
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            self._observe(spec, event, "allow", elapsed_ms, ctx)
        return HookDecision(action="allow")

    def _record_serial_failure(
        self,
        spec: HookSpec,
        event: str,
        ctx: HookContext,
        errors: list[HookError],
        error: HookError,
    ) -> None:
        errors.append(error)
        HOOK_EXECUTIONS.labels(event=event, hook=spec.name, outcome=error.outcome).inc()
        HOOK_DURATION.labels(event=event, hook=spec.name).observe(error.elapsed_ms / 1000.0)
        logger.warning(
            "hook failure event=%s hook=%s action=%s outcome=%s elapsed_ms=%d "
            "error_type=%s message=%s arguments=%s",
            event,
            spec.name,
            spec.failure_policy,
            error.outcome,
            error.elapsed_ms,
            error.error_type,
            error.message,
            _log_arguments(ctx),
        )

    def _observe(
        self,
        spec: HookSpec,
        event: str,
        action: str,
        elapsed_ms: int,
        ctx: HookContext,
    ) -> None:
        HOOK_EXECUTIONS.labels(event=event, hook=spec.name, outcome=action).inc()
        HOOK_DURATION.labels(event=event, hook=spec.name).observe(elapsed_ms / 1000.0)
        logger.info(
            "hook event=%s hook=%s action=%s elapsed_ms=%d arguments=%s",
            event,
            spec.name,
            action,
            elapsed_ms,
            _log_arguments(ctx),
        )


# ── 进程内单例 ──────────────────────────────────────────────────────────────
# 默认 enabled=False：HOOKS_ENABLED=false 时零读取、零行为变化（issue #474 §3）。
# 启动路径（main.lifespan / initialize_from_settings）在开关打开后才 load+启用；
# 测试/SDK 可用 set_hook_bus 注入自建总线。
_bus = HookBus(enabled=False)


def get_hook_bus() -> HookBus:
    return _bus


def set_hook_bus(bus: HookBus | None) -> HookBus:
    """替换进程内总线（测试/SDK 用）；None 恢复默认关闭总线。"""
    global _bus
    _bus = bus if bus is not None else HookBus(enabled=False)
    return _bus
