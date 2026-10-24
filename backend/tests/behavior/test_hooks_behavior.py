"""Hooks 行为测试（issue #474 §8）—— 真实插入点上的 allow/deny/modify/ask、隔离与默认关闭。

驱动方式与 test_hitl_approval_gate.py 同款：假 LLM（chat.completions.create）+
直接驱动 Executor；工具层用 ToolResult 替身，不碰真实工具/DB。

覆盖的验收项：
- HOOKS_ENABLED=false（HookBus(enabled=False)）：hook 零调用，工具照常执行；
- tools/pre_execute deny：handler 零调用、事件 error_type=hook_denied；
- tools/pre_execute modify：handler 收到修改后的参数；
- tools/pre_execute ask + approval_mode=never：仍强制进入审批门（ApprovalRequired），
  未批准前 handler 零调用；无审批通道 → deny；
- tools/post_execute modify/deny：ledger 记录最终结果、事件内容同步替换；
- hook 超时 fail_open：工具成功 + hook_error 事件字段齐全、按时收敛；
- agent/pre_step deny：本轮不调用 LLM、步骤跳过、轮次正常结束；
- agent/pre_step modify messages：改写后的 messages 进入 LLM 调用；
- agent/post_step serial 观察：payload 带最终步骤结果；
- agent/turn_start / turn_stopping deny / turn_end：loop 层事件真实发出，
  turn_stopping deny 会让循环继续（planner 被再次调用）。

变异点（删掉对应接线即红）：
- executor 不在审批预检前 emit pre_execute → deny/modify/ask 用例红；
- pre_execute 在 approval 之后 → ask + never 用例红（暂停不触发）；
- post_execute 在落账之后 → ledger 结果不变 → post modify 用例红；
- loop 不在步前 emit pre_step → pre_step 用例红；
- turn_stopping deny 后仍 break → planner 调用次数 = 1 → 用例红；
- bus 默认启用 → 默认关闭用例红。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发工具注册
from app.agent import approval
from app.agent import hooks as hooks_pkg
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.executor import Executor
from app.agent.hooks import HookBus, HookDecision, HookSpec
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolResult, tool_registry
from app.rag.retriever import HybridRetriever

# ── 假件 ─────────────────────────────────────────────────────────────────────


class FakeMemory:
    def __init__(self):
        self.results: dict = {}
        self.interactions: list[tuple[str, str]] = []

    def reset_working(self) -> None:
        self.results = {}

    def get_all_results(self) -> dict:
        return dict(self.results)

    def store_step_result(self, step_id, result) -> None:
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_experience(self, **kwargs) -> None:
        pass

    async def record_interaction(self, user_input, assistant_response) -> None:
        self.interactions.append((user_input, assistant_response))


def _client_with_tool_calls(calls_spec, capture: dict | None = None):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        if capture is not None:
            capture.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tcs = []
        for name, args_json, cid in calls_spec:
            tc = MagicMock()
            tc.id = cid
            tc.function.name = name
            tc.function.arguments = args_json
            tcs.append(tc)
        msg.tool_calls = tcs
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _client_plain(content: str = "步骤结论", capture: dict | None = None):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        if capture is not None:
            capture.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = content
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _cfg(**overrides) -> AgentConfig:
    base = dict(
        enable_reflection=False,
        enable_memory=False,
        enable_skills=False,
        enable_experience=False,
        enable_thinking=False,
        disabled_tools=[],
    )
    base.update(overrides)
    return AgentConfig(**base)


def _executor(client=None, *, cfg: AgentConfig | None = None, memory=None) -> Executor:
    return Executor(client or _client_plain(), cfg or _cfg(), memory or FakeMemory())


def _spec(name: str, event: str, **overrides) -> HookSpec:
    base = dict(
        name=name,
        event=event,
        module=f"app.agent.hooks.builtin.{name}",
        priority=100,
        timeout_seconds=2.0,
        failure_policy="fail_open",
    )
    base.update(overrides)
    return HookSpec(**base)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))
    original = hooks_pkg.get_hook_bus()
    yield
    hooks_pkg.set_hook_bus(original)


@pytest.fixture
def bus():
    bus = HookBus(enabled=True)
    hooks_pkg.set_hook_bus(bus)
    return bus


async def _drive(gen) -> tuple[list, BaseException | None]:
    events: list = []
    try:
        async for ev in gen:
            events.append(ev)
    except BaseException as e:  # noqa: BLE001 — 判别用例要拿到控制流异常本身
        return events, e
    return events, None


async def _run_step_once(executor: Executor, *, step_id: str = "s1"):
    step = PlanStep(id=step_id, description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    events, exc = await _drive(
        executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=2,
            user_id=7,
            enable_thinking=False,
        )
    )
    return events, exc, step, plan


async def _fake_tool_result(*args, **kwargs):
    return ToolResult.ok(data={"answer": "ok"})


def _patch_registry(monkeypatch, fn=None) -> AsyncMock:
    mock = (
        AsyncMock(side_effect=fn) if fn is not None else AsyncMock(return_value=ToolResult.ok("ok"))
    )
    monkeypatch.setattr(tool_registry, "execute_detailed", mock)
    return mock


def _patch_ledger(monkeypatch):
    """tool_ledger 三件套替身，返回 record_result 的 AsyncMock 供读回。"""
    from app.agent import tool_ledger

    monkeypatch.setattr(tool_ledger, "register_calls", AsyncMock())
    monkeypatch.setattr(tool_ledger, "mark_dispatched", AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(tool_ledger, "record_result", record)
    return record


# ── 默认关闭 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_bus_zero_hook_calls_and_tool_still_runs(monkeypatch):
    hooks_pkg.set_hook_bus(HookBus(enabled=False))
    calls: list[str] = []

    disabled_bus = hooks_pkg.get_hook_bus()

    async def pre(ctx, nxt):
        calls.append("pre")
        return await nxt(ctx.payload)

    disabled_bus.register(_spec("counter", "tools/pre_execute"), pre)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert calls == [], "HOOKS_ENABLED=false 时 hook 必须零调用"
    assert execute_mock.await_count == 1, "工具照常执行"
    assert any(e.type == "tool_result" for e in events), "行为与基线一致"


# ── tools/pre_execute ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_execute_deny_blocks_handler_and_reports_hook_denied(monkeypatch, bus):
    async def deny_hook(ctx, nxt):
        assert ctx.payload["tool_name"] == "search_knowledge_base"
        assert ctx.payload["tool_call_id"] == "c1"
        assert ctx.payload["risk_level"] == "low"
        return HookDecision(action="deny", reason="策略禁止")

    bus.register(_spec("deny_all", "tools/pre_execute"), deny_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert execute_mock.await_count == 0, "hook deny 时工具 handler 零调用"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "hook_denied"
    assert "策略禁止" in errs[0].content
    assert ToolResult.fail("hook_denied", "策略禁止").error.code == "hook_denied"


@pytest.mark.asyncio
async def test_pre_execute_modify_rewrites_arguments(monkeypatch, bus):
    async def modify_hook(ctx, nxt):
        payload = dict(ctx.payload)
        payload["arguments"] = {"q": "改写后的查询"}
        return await nxt(payload)

    bus.register(_spec("rewrite", "tools/pre_execute"), modify_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"原始"}', "c1")]))

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert execute_mock.await_count == 1
    # execute_detailed(name, arguments, **context) —— arguments 是第二个位置参数
    call_args = execute_mock.await_args.args
    assert call_args[1] == {"q": "改写后的查询"}, "handler 必须收到修改后的参数"
    assert any(e.type == "tool_result" for e in events)


@pytest.mark.asyncio
async def test_pre_execute_ask_forces_approval_even_when_mode_never(monkeypatch, bus):
    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="需要人工确认")

    bus.register(_spec("ask", "tools/pre_execute"), ask_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("web_search", '{"action":"fetch","url":"https://x"}', "c1")]),
        cfg=_cfg(approval_mode="never"),
    )
    requests: list[approval.ApprovalRequest] = []

    async def _check(request):
        requests.append(request)
        return "paused"

    executor.approval_check = _check

    events, exc, _, _ = await _run_step_once(executor)

    assert isinstance(exc, approval.ApprovalRequired), "ask 必须强制走审批门（即使 mode=never）"
    assert requests and requests[0].tool_name == "web_search"
    assert execute_mock.await_count == 0, "未批准前 handler 零调用"


@pytest.mark.asyncio
async def test_pre_execute_ask_without_channel_denies(monkeypatch, bus):
    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="需要人工确认")

    bus.register(_spec("ask", "tools/pre_execute"), ask_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("web_search", '{"action":"fetch","url":"https://x"}', "c1")])
    )
    executor.approval_check = None

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert execute_mock.await_count == 0, "无审批通道时 ask 按 deny 处理"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "hook_denied"


@pytest.mark.asyncio
async def test_pre_execute_hook_runs_before_approval_precheck(monkeypatch, bus):
    """外置 hook 必须先于审批预检执行（issue §5.5 关键修正的判别用例）。"""
    order: list[str] = []

    async def hook(ctx, nxt):
        order.append("hook")
        return await nxt(ctx.payload)

    bus.register(_spec("order", "tools/pre_execute"), hook)
    _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("execute_python", '{"code":"1"}', "c1")]),
        cfg=_cfg(approval_mode="always"),
    )

    async def _check(request):
        order.append("approval")
        return "paused"

    executor.approval_check = _check

    _, exc, _, _ = await _run_step_once(executor)

    assert isinstance(exc, approval.ApprovalRequired)
    assert order == ["hook", "approval"], "hook 必须在审批预检之前看到这次调用"


@pytest.mark.asyncio
async def test_pre_execute_ask_sticky_deny_is_not_bypassed_by_mode_never(monkeypatch, bus):
    """ask 强制审批后，人类拒绝的粘滞决定必须生效——mode=never 不得绕过。"""

    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="需要人工确认")

    bus.register(_spec("ask", "tools/pre_execute"), ask_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("web_search", '{"action":"fetch","url":"https://x"}', "c1")]),
        cfg=_cfg(approval_mode="never"),
    )
    executor.approval_check = AsyncMock(return_value="paused")
    args = {"action": "fetch", "url": "https://x"}
    executor.approval_decisions = [
        {
            "tool_name": "web_search",
            "args_hash": approval.args_fingerprint(args),
            "decision": "deny",
        }
    ]

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None, "粘滞 deny 不应再次暂停"
    assert execute_mock.await_count == 0, "粘滞 deny 必须阻止执行"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "approval_denied"


@pytest.mark.asyncio
async def test_pre_execute_deny_skips_approval_pause(monkeypatch, bus):
    """hook deny 的调用不得再进入审批暂停（注定不执行的调用不该打扰人类）。"""

    async def deny_hook(ctx, nxt):
        return HookDecision(action="deny", reason="策略禁止")

    bus.register(_spec("deny", "tools/pre_execute"), deny_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("execute_python", '{"code":"1"}', "c1")]),
        cfg=_cfg(approval_mode="always"),
    )
    approval_check = AsyncMock(return_value="paused")
    executor.approval_check = approval_check

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert approval_check.await_count == 0, "deny 的调用不得触发审批暂停"
    assert execute_mock.await_count == 0
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "hook_denied"


@pytest.mark.asyncio
async def test_pre_execute_ask_approved_executes(monkeypatch, bus):
    """ask → 首次暂停（审批通道被调用）→ 粘滞 approve 后执行且不再暂停。"""

    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="需要人工确认")

    bus.register(_spec("ask", "tools/pre_execute"), ask_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("web_search", '{"action":"fetch","url":"https://x"}', "c1")]),
        cfg=_cfg(approval_mode="never"),
    )
    approval_check = AsyncMock(return_value="paused")
    executor.approval_check = approval_check
    args = {"action": "fetch", "url": "https://x"}

    # 第一次：无粘滞决定 → 必须暂停（证明 forced 接线真实生效）。
    _, first_exc, _, _ = await _run_step_once(executor)
    assert isinstance(first_exc, approval.ApprovalRequired)
    assert approval_check.await_count == 1
    assert execute_mock.await_count == 0

    # 人类批准（粘滞 approve）→ 第二次执行通过且不再暂停。
    executor.approval_decisions = [
        {
            "tool_name": "web_search",
            "args_hash": approval.args_fingerprint(args),
            "decision": "approve",
        }
    ]
    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert execute_mock.await_count == 1
    assert execute_mock.await_args.args[1] == args
    assert approval_check.await_count == 1, "粘滞 approve 不应再次暂停"


@pytest.mark.asyncio
async def test_pre_execute_ask_degraded_denies(monkeypatch, bus):
    """ask 强制审批但审批服务不可用（降级）→ 绝不静默执行。"""

    async def ask_hook(ctx, nxt):
        return HookDecision(action="ask", reason="需要人工确认")

    bus.register(_spec("ask", "tools/pre_execute"), ask_hook)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(
        _client_with_tool_calls([("web_search", '{"action":"fetch","url":"https://x"}', "c1")]),
        cfg=_cfg(approval_mode="never"),
    )
    executor.approval_check = AsyncMock(return_value="unavailable")

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert execute_mock.await_count == 0
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "approval_unavailable"


# ── tools/post_execute ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_execute_modify_replaces_result_and_ledger(monkeypatch, bus):
    async def modify_hook(ctx, nxt):
        payload = dict(ctx.payload)
        result = dict(payload["result"])
        result["content"] = "被 hook 替换的结果"
        payload["result"] = result
        return await nxt(payload)

    bus.register(_spec("post_rewrite", "tools/post_execute"), modify_hook)
    _patch_registry(monkeypatch, fn=_fake_tool_result)
    record = _patch_ledger(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))
    executor.tool_ledger_task_id = "task-h"

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    results = [e for e in events if e.type == "tool_result"]
    assert results and results[0].content == "被 hook 替换的结果"
    assert record.await_count == 1
    ledger_kwargs = record.await_args.kwargs
    assert ledger_kwargs["result"] == "被 hook 替换的结果", "ledger 记录最终结果而非原始结果"
    assert ledger_kwargs["success"] is True


@pytest.mark.asyncio
async def test_post_execute_deny_replaces_with_refusal(monkeypatch, bus):
    async def deny_hook(ctx, nxt):
        return HookDecision(action="deny", reason="结果不合规")

    bus.register(_spec("post_deny", "tools/post_execute"), deny_hook)
    _patch_registry(monkeypatch, fn=_fake_tool_result)
    record = _patch_ledger(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))
    executor.tool_ledger_task_id = "task-h"

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "hook_denied"
    assert "结果不合规" in errs[0].content
    assert record.await_args.kwargs["success"] is False


# ── 超时 / 隔离 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hook_timeout_fail_open_tool_succeeds_with_hook_error_event(monkeypatch, bus):
    async def slow(ctx, nxt):
        await asyncio.sleep(3)

    bus.register(_spec("slow", "tools/pre_execute", timeout_seconds=0.5), slow)
    execute_mock = _patch_registry(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))

    start = time.perf_counter()
    events, exc, _, _ = await _run_step_once(executor)
    elapsed = time.perf_counter() - start

    assert exc is None
    assert elapsed < 2.5, "超时必须按时收敛，不得拖死轮次"
    assert execute_mock.await_count == 1, "fail_open：工具仍然执行"
    hook_errors = [e for e in events if e.type == "hook_error"]
    assert hook_errors, "hook 超时必须外发 hook_error（不得静默）"
    err = hook_errors[0]
    assert err.hook == "slow"
    assert err.event_name == "tools/pre_execute"
    assert err.action == "fail_open"
    assert err.elapsed_ms is not None and err.elapsed_ms >= 400
    sse = err.to_sse_dict()
    assert sse["hook"] == "slow" and sse["event_name"] == "tools/pre_execute"
    assert sse["action"] == "fail_open" and sse["elapsed_ms"] >= 400


# ── agent/pre_step / post_step（executor 内真实边界）────────────────────────


@pytest.mark.asyncio
async def test_pre_step_deny_skips_turn_without_llm(bus):
    async def deny_hook(ctx, nxt):
        assert "messages" in ctx.payload and ctx.payload["step"]["id"]
        return HookDecision(action="deny", reason="输入不合规")

    bus.register(_spec("pre_deny", "agent/pre_step"), deny_hook)
    captured: dict = {}
    client = _client_plain(capture=captured)
    executor = _executor(client)
    plan = Plan(
        id="p1",
        goal="g",
        steps=[PlanStep(id="s1", description="一"), PlanStep(id="s2", description="二")],
    )

    events = [e async for e in executor.execute(plan, organization_id=2, user_id=7)]

    assert captured == {}, "pre_step deny 后本轮不得调用 LLM"
    assert plan.is_complete, "轮次应正常结束（步骤跳过）"
    assert all(s.status == "skipped" for s in plan.steps)
    assert any(e.type == "tool_error" and e.error_type == "hook_denied" for e in events)


@pytest.mark.asyncio
async def test_pre_step_modify_messages_reaches_llm(bus):
    async def rewrite(ctx, nxt):
        payload = dict(ctx.payload)
        payload["messages"] = [{"role": "system", "content": "HOOK-REWRITTEN"}]
        return await nxt(payload)

    bus.register(_spec("pre_rewrite", "agent/pre_step"), rewrite)
    captured: dict = {}
    executor = _executor(_client_plain(capture=captured))

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert captured["messages"], "LLM 必须被调用"
    assert captured["messages"][0][0]["content"] == "HOOK-REWRITTEN"


@pytest.mark.asyncio
async def test_post_step_serial_observes_final_result(bus):
    observed: list[dict] = []

    async def observe(ctx, nxt):
        assert nxt is None, "post_step 是 serial 事件"
        observed.append(dict(ctx.payload))
        return HookDecision(action="deny", reason="ignored")

    bus.register(_spec("post_observe", "agent/post_step"), observe)
    executor = _executor(_client_plain("最终步骤结论"))
    step = PlanStep(id="s1", description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])

    async for _ in executor._execute_single_step(
        step,
        plan,
        None,
        2,
        7,
        enable_thinking=False,
    ):
        pass

    assert observed, "post_step 必须被调用"
    assert observed[0]["result"]["status"] == "completed"
    assert "最终步骤结论" in (observed[0]["result"]["result"] or "")
    assert step.status == "completed"


@pytest.mark.asyncio
async def test_post_execute_runs_on_tool_exception(monkeypatch, bus):
    """工具抛异常/超时同样是执行结果：post_execute 必须被观察到。"""
    observed: list[dict] = []

    async def observe(ctx, nxt):
        observed.append(dict(ctx.payload["result"]))
        return await nxt(ctx.payload)

    bus.register(_spec("post_exc", "tools/post_execute"), observe)
    monkeypatch.setattr(
        tool_registry,
        "execute_detailed",
        AsyncMock(side_effect=TimeoutError("tool timed out")),
    )
    record = _patch_ledger(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))
    executor.tool_ledger_task_id = "task-h"

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    assert observed and observed[0]["success"] is False
    assert observed[0]["error_type"] == "TimeoutError"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "TimeoutError"
    assert record.await_args.kwargs["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_post_execute_deny_on_tool_exception_marks_hook_denied(monkeypatch, bus):
    """异常路径上的 post_execute deny：ledger/事件都记 hook_denied。"""

    async def deny_hook(ctx, nxt):
        return HookDecision(action="deny", reason="异常结果不合规")

    bus.register(_spec("post_exc_deny", "tools/post_execute"), deny_hook)
    monkeypatch.setattr(
        tool_registry,
        "execute_detailed",
        AsyncMock(side_effect=TimeoutError("tool timed out")),
    )
    record = _patch_ledger(monkeypatch)
    executor = _executor(_client_with_tool_calls([("search_knowledge_base", '{"q":"x"}', "c1")]))
    executor.tool_ledger_task_id = "task-h"

    events, exc, _, _ = await _run_step_once(executor)

    assert exc is None
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "hook_denied"
    assert "异常结果不合规" in errs[0].content
    assert record.await_args.kwargs["error_type"] == "hook_denied"


def test_hook_session_id_strips_session_prefix():
    executor = _executor()
    executor.config.agent_id = "session:abc-123"
    assert executor._hook_session_id() == "abc-123"
    executor.config.agent_id = "default"
    assert executor._hook_session_id() is None


# ── loop 层 turn 事件 ───────────────────────────────────────────────────────


class _LoopPlanner:
    def __init__(self):
        self.calls = 0

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls += 1
        yield AgentEvent(type="plan_start", plan_id=f"plan-{self.calls}")
        yield AgentEvent(
            type="plan_step",
            plan_id=f"plan-{self.calls}",
            plan_step_id="s1",
            content="步骤一",
        )
        yield AgentEvent(type="plan_complete", plan_id=f"plan-{self.calls}", plan_goal="目标")


class _LoopExecutor:
    def __init__(self, memory=None):
        self.calls = 0
        self.memory = memory

    def reset_for_request(self):
        pass

    async def execute(
        self,
        plan,
        history=None,
        organization_id=1,
        user_id=0,
        enable_thinking=True,
        ctx=None,
        on_step_done=None,
        human_guidance="",
        document_context="",
    ):
        self.calls += 1
        for s in plan.steps:
            s.status = "completed"
            plan.completed_steps += 1
            if self.memory is not None:
                self.memory.store_step_result(
                    s.id,
                    {"description": s.description, "status": "completed", "result": "答案"},
                )
        yield AgentEvent(type="chunk", content="答案")


def _make_loop(config: AgentConfig | None = None):
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(
        MagicMock(),
        config or _cfg(enable_planning=True, enable_checkpoint=False),
        organization_id=3,
        user_id=9,
    )
    loop.memory = FakeMemory()
    loop._planner = _LoopPlanner()
    loop._executor = _LoopExecutor(memory=loop.memory)
    loop._write_experiences = AsyncMock()
    return loop


@pytest.fixture(autouse=True)
def _no_ctx_persistence(monkeypatch):
    from app.agent.exec_context import ExecutionContext

    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)


async def _drive_loop(loop, query="测试问题"):
    return [e async for e in loop.run(query)]


@pytest.mark.asyncio
async def test_loop_emits_turn_start_and_turn_end(bus):
    seen: list[tuple[str, dict]] = []

    async def recorder(ctx, nxt):
        seen.append((ctx.event, dict(ctx.payload)))
        return await nxt(ctx.payload)

    bus.register(_spec("turn_start_rec", "agent/turn_start"), recorder)
    bus.register(_spec("turn_end_rec", "agent/turn_end"), recorder)

    loop = _make_loop()
    events = await _drive_loop(loop)

    names = [name for name, _ in seen]
    assert names == ["agent/turn_start", "agent/turn_end"], f"实际: {names}"
    start_payload = seen[0][1]
    assert start_payload["session_id"] is None
    assert start_payload["query"] == "测试问题"
    end_payload = seen[1][1]
    assert end_payload["status"] == "completed"
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_turn_stopping_deny_continues_loop(bus):
    decisions = iter([HookDecision(action="deny", reason="再想一步"), HookDecision(action="allow")])
    stopping_calls: list[dict] = []

    async def stopper(ctx, nxt):
        stopping_calls.append(dict(ctx.payload))
        return next(decisions, HookDecision(action="allow"))

    bus.register(_spec("stopper", "agent/turn_stopping"), stopper)

    loop = _make_loop()
    events = await _drive_loop(loop)

    assert len(stopping_calls) == 2, "turn_stopping 每次自然结束前都会询问"
    assert loop._planner.calls == 2, "deny 后循环继续（重新规划）"
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_turn_stopping_deny_is_bounded(bus):
    async def always_deny(ctx, nxt):
        return HookDecision(action="deny", reason="永不停止")

    bus.register(_spec("always_deny", "agent/turn_stopping"), always_deny)

    loop = _make_loop()
    events = await _drive_loop(loop)

    assert any(e.type == "done" for e in events), "必须被硬上限收敛，不得无限循环"
    from app.agent.loop import HOOK_TURN_STOPPING_MAX_DENIALS

    assert loop._planner.calls == HOOK_TURN_STOPPING_MAX_DENIALS + 1


@pytest.mark.asyncio
async def test_turn_end_emitted_on_cancel(bus):
    """取消也是轮次结束：turn_start/turn_end 必须成对（status=cancelled）。"""
    seen: list[tuple[str, object]] = []

    async def recorder(ctx, nxt):
        seen.append((ctx.event, ctx.payload.get("status")))
        return await nxt(ctx.payload)

    bus.register(_spec("rec_start", "agent/turn_start"), recorder)
    bus.register(_spec("rec_end", "agent/turn_end"), recorder)

    loop = _make_loop()

    async def _cancel():
        return True

    loop.cancel_check = _cancel
    events = await _drive_loop(loop)

    assert ("agent/turn_start", None) in seen
    assert ("agent/turn_end", "cancelled") in seen
    assert any(e.type == "run_cancelled" for e in events)


@pytest.mark.asyncio
async def test_turn_end_emitted_on_fresh_plan_pause(bus):
    """plan_approval_mode=always 的新鲜计划暂停：turn_start/turn_end 成对（paused）。"""
    seen: list[tuple[str, object]] = []

    async def recorder(ctx, nxt):
        seen.append((ctx.event, ctx.payload.get("status")))
        return await nxt(ctx.payload)

    bus.register(_spec("rec_start2", "agent/turn_start"), recorder)
    bus.register(_spec("rec_end2", "agent/turn_end"), recorder)

    loop = _make_loop(
        config=_cfg(enable_planning=True, enable_checkpoint=False, plan_approval_mode="always")
    )

    async def _fake_pause(plan, run_state_live):
        loop.paused_for_plan_approval = True
        yield AgentEvent(type="plan_approval_required", content="计划审批挂起")

    loop._pause_for_plan_approval = _fake_pause
    events = await _drive_loop(loop)

    assert ("agent/turn_end", "paused") in seen
    assert any(e.type == "plan_approval_required" for e in events)


# ── SSE 事件字段 ────────────────────────────────────────────────────────────


def test_hook_error_event_serialization_is_additive():
    event = AgentEvent(
        type="hook_error",
        content="hook 超时，已放行",
        hook="audit_tool_calls",
        event_name="tools/pre_execute",
        action="fail_open",
        elapsed_ms=2001,
    )
    data = event.to_sse_dict()
    assert data["type"] == "hook_error"
    assert data["hook"] == "audit_tool_calls"
    assert data["event_name"] == "tools/pre_execute"
    assert data["action"] == "fail_open"
    assert data["elapsed_ms"] == 2001
    # 其余事件类型不携带这些键（既有载荷零变化）
    plain = AgentEvent(type="done").to_sse_dict()
    assert "hook" not in plain and "event_name" not in plain
