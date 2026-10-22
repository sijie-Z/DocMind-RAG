"""Hooks 总线（issue #474）—— waterfall/serial 语义、顺序、超时、异常隔离、递归防护。

被测契约（backend/app/agent/hooks/registry.py 模块 docstring + issue #474 §5.2）：

- waterfall：handler 收到 (ctx, next)；`await next(payload)` 继续链路，返回最终
  决策；不调用 next 即短路，自己的返回值就是最终决策；返回 None 视为 allow 且
  payload 不变。
- serial：next 传 None，返回值忽略，顺序 await（仅 agent/post_step、agent/turn_end）。
- 顺序：低 priority 先执行；同优先级按注册顺序（稳定排序）。
- 超时：每 hook 独立超时，超时按 failure_policy 收敛；fail_open 继续、fail_closed
  deny；任何 failure 都产出 HookError（不静默）。
- 递归防护：hook 内再次 emit 直接 allow（最大深度 1），内部 hook 不被调用。
- 默认关闭：HookBus(enabled=False) 时注册过的 handler 零调用、emit 直接 allow。
- 脱敏：日志用参数必须把 password/token/secret/key 字段打码。

变异点（本文件即判别器）：
- 顺序按注册而非 priority → test_priority_then_registration_order 红；
- waterfall 短路仍继续跑后续 hook → test_waterfall_short_circuit 红；
- modify 的 payload 不向后续 hook 传递 → test_waterfall_modify_chains 红；
- None 被当成 deny → test_waterfall_none_is_allow 红；
- serial 收到 next → test_serial_receives_none_next 红；
- 超时/异常不按 failure_policy 收敛 → 各 timeout/exception 用例红；
- 未做递归防护 → test_recursion_guard 红；
- enabled=False 仍调用 handler → test_disabled_bus_zero_calls 红；
- 脱敏漏字段 → test_redact_arguments 红。
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.agent.hooks import (
    HookBus,
    HookContext,
    HookDecision,
    HookError,
    HookSpec,
    hook_depth_active,
    hook_error_events,
    redact_arguments,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _ctx(event: str, **payload) -> HookContext:
    return HookContext(event=event, payload=payload)


def _recorder(trace: list[str], label: str, decision: HookDecision | None = None):
    async def _handler(ctx: HookContext, nxt):
        trace.append(label)
        if nxt is not None:
            return await nxt(ctx.payload)
        return decision

    return _handler


def _spec(
    name: str,
    event: str = "agent/pre_step",
    *,
    priority: int = 100,
    timeout: float = 2.0,
    policy: str = "fail_open",
) -> HookSpec:
    return HookSpec(
        name=name,
        event=event,
        module=f"app.agent.hooks.builtin.{name}",
        priority=priority,
        timeout_seconds=timeout,
        failure_policy=policy,
    )


# ── 顺序 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_priority_then_registration_order():
    bus = HookBus()
    trace: list[str] = []
    # 注册顺序：b(priority=200) → a(100) → c(100)
    bus.register(_spec("b", priority=200), _recorder(trace, "b"))
    bus.register(_spec("a", priority=100), _recorder(trace, "a"))
    bus.register(_spec("c", priority=100), _recorder(trace, "c"))

    decision, errors = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert decision.action == "allow"
    assert errors == []
    assert trace == ["a", "c", "b"], "低 priority 先行；同优先级按注册顺序"


# ── waterfall ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_waterfall_modify_chains_payload():
    bus = HookBus()
    seen: list[dict] = []

    async def first(ctx, nxt):
        payload = dict(ctx.payload)
        payload["messages"] = [{"role": "system", "content": "REWRITTEN"}]
        return await nxt(payload)

    async def second(ctx, nxt):
        seen.append(dict(ctx.payload))
        return await nxt(ctx.payload)

    bus.register(_spec("first"), first)
    bus.register(_spec("second", priority=200), second)

    initial = {"messages": [{"role": "user", "content": "原始"}]}
    decision, _ = await bus.emit(
        "agent/pre_step", HookContext(event="agent/pre_step", payload=initial)
    )
    assert seen == [{"messages": [{"role": "system", "content": "REWRITTEN"}]}]
    assert decision.payload["messages"][0]["content"] == "REWRITTEN"
    assert initial["messages"][0]["content"] == "原始", "不得原地修改调用方 payload"


@pytest.mark.asyncio
async def test_waterfall_short_circuit():
    bus = HookBus()
    trace: list[str] = []

    async def blocker(ctx, nxt):
        trace.append("blocker")
        return HookDecision(action="deny", reason="policy")

    bus.register(_spec("blocker", event="tools/pre_execute", priority=10), blocker)
    bus.register(_spec("never", event="tools/pre_execute", priority=20), _recorder(trace, "never"))

    decision, _ = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
    assert decision.action == "deny"
    assert decision.reason == "policy"
    assert trace == ["blocker"], "不调用 next 即短路，后续 hook 不得执行"


@pytest.mark.asyncio
async def test_waterfall_none_is_allow():
    bus = HookBus()
    called: list[str] = []

    async def none_handler(ctx, nxt):
        called.append("none")
        await nxt(ctx.payload)  # 继续链路

    async def tail(ctx, nxt):
        called.append("tail")
        return HookDecision(action="allow", payload=dict(ctx.payload, touched=True))

    bus.register(_spec("none", event="agent/turn_start"), none_handler)
    bus.register(_spec("tail", event="agent/turn_start", priority=200), tail)

    decision, errors = await bus.emit("agent/turn_start", _ctx("agent/turn_start", q="x"))
    assert decision.action == "allow"
    assert decision.payload["touched"] is True
    assert called == ["none", "tail"]
    assert errors == []


@pytest.mark.asyncio
async def test_waterfall_none_without_next_is_allow_with_unchanged_payload():
    bus = HookBus()

    async def none_handler(ctx, nxt):
        return None

    bus.register(_spec("none", event="agent/turn_start"), none_handler)
    decision, _ = await bus.emit("agent/turn_start", _ctx("agent/turn_start", q="x"))
    assert decision.action == "allow"
    assert decision.payload == {"q": "x"}


# ── serial ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_receives_none_next_and_return_ignored():
    bus = HookBus()
    calls: list[tuple[str, object]] = []

    async def h1(ctx, nxt):
        calls.append(("h1", nxt))
        return HookDecision(action="deny", reason="should be ignored")

    async def h2(ctx, nxt):
        calls.append(("h2", nxt))

    bus.register(_spec("h1", event="agent/post_step"), h1)
    bus.register(_spec("h2", event="agent/post_step", priority=200), h2)

    decision, errors = await bus.emit("agent/post_step", _ctx("agent/post_step"))
    assert [name for name, _ in calls] == ["h1", "h2"], "serial 顺序执行"
    assert all(nxt is None for _, nxt in calls), "serial 的 next 必须为 None"
    assert decision.action == "allow", "observe-only 事件的返回值不可阻断"
    assert errors == []


# ── 超时 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_fail_open_continues_within_bound():
    bus = HookBus()
    trace: list[str] = []

    async def slow(ctx, nxt):
        await asyncio.sleep(3)
        trace.append("slow-finished")

    async def tail(ctx, nxt):
        trace.append("tail")
        return await nxt(ctx.payload)

    bus.register(_spec("slow", timeout=0.3, policy="fail_open"), slow)
    bus.register(_spec("tail", priority=200), tail)

    start = time.perf_counter()
    decision, errors = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    elapsed = time.perf_counter() - start

    assert 0.2 <= elapsed < 1.5, "超时必须按时收敛，不得拖死链路"
    assert decision.action == "allow", "fail_open 放行"
    assert trace == ["tail"], "超时的 hook 不得完成，后续 hook 照常执行"
    assert len(errors) == 1
    assert errors[0].hook == "slow"
    assert errors[0].outcome == "timeout"
    assert errors[0].elapsed_ms >= 200


@pytest.mark.asyncio
async def test_timeout_fail_closed_denies():
    bus = HookBus()

    async def slow(ctx, nxt):
        await asyncio.sleep(3)

    bus.register(_spec("slow", event="tools/pre_execute", timeout=0.2, policy="fail_closed"), slow)
    decision, errors = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
    assert decision.action == "deny"
    assert "slow" in decision.reason
    assert errors and errors[0].outcome == "timeout"


# ── 异常隔离 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exception_fail_open_isolated():
    bus = HookBus()
    trace: list[str] = []

    async def boom(ctx, nxt):
        raise RuntimeError("handler exploded")

    async def tail(ctx, nxt):
        trace.append("tail")
        return await nxt(ctx.payload)

    bus.register(_spec("boom", event="tools/pre_execute", policy="fail_open"), boom)
    bus.register(_spec("tail", event="tools/pre_execute", priority=200), tail)

    decision, errors = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
    assert decision.action == "allow"
    assert trace == ["tail"]
    assert errors[0].error_type == "RuntimeError"
    assert errors[0].outcome == "error"


@pytest.mark.asyncio
async def test_exception_fail_closed_denies():
    bus = HookBus()

    async def boom(ctx, nxt):
        raise ValueError("nope")

    bus.register(_spec("boom", event="tools/pre_execute", policy="fail_closed"), boom)
    decision, errors = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
    assert decision.action == "deny"
    assert "boom" in decision.reason
    assert errors[0].error_type == "ValueError"


@pytest.mark.asyncio
async def test_invalid_decision_is_isolated():
    bus = HookBus()

    async def bad(ctx, nxt):
        return {"action": "allow"}  # 非 HookDecision / None

    bus.register(_spec("bad", policy="fail_open"), bad)
    decision, errors = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert decision.action == "allow"
    assert errors[0].error_type == "invalid_decision"


# ── 默认关闭 / 递归防护 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_bus_zero_calls():
    bus = HookBus(enabled=False)
    calls: list[int] = []

    async def handler(ctx, nxt):
        calls.append(1)
        return await nxt(ctx.payload)

    bus.register(_spec("h"), handler)
    decision, errors = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert decision.action == "allow"
    assert errors == []
    assert calls == [], "HOOKS_ENABLED=false 时 hook 必须零调用"


@pytest.mark.asyncio
async def test_recursion_guard_blocks_reentry():
    bus = HookBus()
    inner_calls: list[int] = []
    outer_decision: list[HookDecision] = []

    async def outer(ctx, nxt):
        # hook 内部（模拟它调用工具/再次触发事件）不得递归执行 hook
        decision, errors = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
        outer_decision.append(decision)
        assert errors == []
        return await nxt(ctx.payload)

    async def inner(ctx, nxt):
        inner_calls.append(1)
        return await nxt(ctx.payload)

    bus.register(_spec("outer"), outer)
    bus.register(_spec("inner", event="tools/pre_execute"), inner)

    decision, _ = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert decision.action == "allow"
    assert inner_calls == [], "深度 1 时的再次 emit 必须直接 allow"
    assert outer_decision[0].action == "allow"


@pytest.mark.asyncio
async def test_depth_contextvar_resets_after_emit():
    bus = HookBus()

    async def handler(ctx, nxt):
        return await nxt(ctx.payload)

    bus.register(_spec("h"), handler)
    await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert hook_depth_active() is False


# ── 脱敏 ────────────────────────────────────────────────────────────────────


def test_redact_arguments_masks_sensitive_fields():
    raw = {
        "query": "正常内容",
        "password": "hunter2",
        "api_token": "tk-123",
        "SECRET": "s3cr3t",
        "key": "plain-key-value",
        "api_key": "k-1",
        "nested": {"token": "inner", "keep": "ok"},
        "items": [{"secret_key": "x"}, {"keep": "y"}],
    }
    out = redact_arguments(raw)
    assert out["password"] == "***"
    assert out["api_token"] == "***"
    assert out["SECRET"] == "***"
    assert out["key"] == "***", "issue §5.6 的 key 字段必须打码（字面 key）"
    assert out["api_key"] == "***"
    assert out["nested"]["token"] == "***"
    assert out["nested"]["keep"] == "ok"
    assert out["items"][0]["secret_key"] == "***"
    assert out["items"][1]["keep"] == "y"
    assert out["query"] == "正常内容"


@pytest.mark.asyncio
async def test_hook_exception_text_does_not_leak_arguments(caplog):
    """hook 抛异常时，异常文本可能内嵌入参（密钥）——不得进入 HookError/日志/SSE。"""
    bus = HookBus()

    async def boom(ctx, nxt):
        raise ValueError(f"bad args: {ctx.payload.get('arguments')}")

    bus.register(_spec("boom", event="tools/pre_execute", policy="fail_open"), boom)
    ctx = HookContext(
        event="tools/pre_execute",
        payload={"tool_name": "t", "arguments": {"password": "hunter2", "q": "x"}},
    )
    with caplog.at_level(logging.INFO, logger="docmind.hooks"):
        decision, errors = await bus.emit("tools/pre_execute", ctx)

    assert decision.action == "allow"
    assert errors and errors[0].error_type == "ValueError"
    assert "hunter2" not in errors[0].message
    assert "hunter2" not in caplog.text
    assert "hunter2" not in hook_error_events(errors)[0].to_sse_dict()["content"]


@pytest.mark.asyncio
async def test_outer_timeout_does_not_rerun_downstream():
    """外层 hook 在 next 在途时超时：下游只能启动一次（不得重复副作用）。"""
    bus = HookBus()
    starts: list[str] = []

    async def outer(ctx, nxt):
        await nxt(ctx.payload)
        await asyncio.sleep(3)  # next 已启动后挂起 → 外层超时

    async def downstream(ctx, nxt):
        starts.append("downstream")
        await asyncio.sleep(0.2)
        return await nxt(ctx.payload)

    bus.register(_spec("outer", event="tools/pre_execute", timeout=0.1, policy="fail_open"), outer)
    bus.register(_spec("downstream", event="tools/pre_execute", priority=200), downstream)

    start = time.perf_counter()
    decision, errors = await bus.emit("tools/pre_execute", _ctx("tools/pre_execute"))
    elapsed = time.perf_counter() - start

    assert starts == ["downstream"], "下游不得因外层超时被二次执行"
    assert decision.action == "allow"
    assert elapsed < 1.5
    assert any(e.hook == "outer" and e.outcome == "timeout" for e in errors)


@pytest.mark.asyncio
async def test_serial_fail_closed_returns_deny():
    bus = HookBus()

    async def boom(ctx, nxt):
        raise RuntimeError("serial boom")

    bus.register(_spec("s", event="agent/post_step", policy="fail_closed"), boom)
    decision, errors = await bus.emit("agent/post_step", _ctx("agent/post_step"))
    assert decision.action == "deny"
    assert errors and errors[0].outcome == "error"


@pytest.mark.asyncio
async def test_ask_on_non_pre_execute_is_invalid_decision():
    bus = HookBus()

    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="not allowed here")

    bus.register(_spec("askbad", event="agent/pre_step", policy="fail_closed"), ask_hook)
    decision, errors = await bus.emit("agent/pre_step", _ctx("agent/pre_step"))
    assert decision.action == "deny"
    assert errors and errors[0].outcome == "invalid_decision"


@pytest.mark.asyncio
async def test_audit_log_redacts_sensitive_arguments(caplog):
    """审计日志必须打码 password/token/secret/key（issue #474 §5.6）。"""
    bus = HookBus()

    async def handler(ctx, nxt):
        return await nxt(ctx.payload)

    bus.register(_spec("audit", event="tools/pre_execute"), handler)
    ctx = HookContext(
        event="tools/pre_execute",
        payload={
            "tool_name": "t",
            "arguments": {"password": "hunter2", "token": "tk-secret", "query": "ok"},
        },
    )
    with caplog.at_level(logging.INFO, logger="docmind.hooks"):
        await bus.emit("tools/pre_execute", ctx)

    assert "hunter2" not in caplog.text
    assert "tk-secret" not in caplog.text
    assert "***" in caplog.text
    assert "ok" in caplog.text


def test_hook_error_events_carry_all_fields():
    """SSE hook_error 字段齐全：hook/event_name/action/elapsed_ms/error_type/content。"""
    errors = [
        HookError(
            hook="audit_tool_calls",
            event="tools/pre_execute",
            action="fail_open",
            outcome="timeout",
            error_type="timeout",
            message="hook audit_tool_calls 执行超时（2.0s）",
            elapsed_ms=2001,
        )
    ]
    events = hook_error_events(errors)
    assert len(events) == 1
    data = events[0].to_sse_dict()
    assert data["type"] == "hook_error"
    assert data["hook"] == "audit_tool_calls"
    assert data["event_name"] == "tools/pre_execute"
    assert data["action"] == "fail_open"
    assert data["elapsed_ms"] == 2001
    assert data["error_type"] == "timeout"
    assert data["content"] == "hook audit_tool_calls 执行超时（2.0s）"
