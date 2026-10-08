"""issue #242 第 7 条：circuit_breaker 状态机 API 接入 LLM 步骤路径的判别性测试。

- 状态机 API 语义（CLOSED / OPEN / HALF_OPEN、allow_request / record_*）；
- executor 判别性：连续失败达阈值 → OPEN → 下一次调用**不再触达 client**
  （毫秒级快速失败；错误码 circuit_open；retry_policy 为 fatal）；
- 恢复路径（状态机层）：OPEN + 恢复窗口过后半开探测成功 → CLOSED；
- 门路径守护（issue #256）：被拦请求不得记账（防窗口自延长）、门早退收尾观测 span；
- 生产常量钉：_LLM_BREAKER 的 threshold=3 / recovery_timeout=30（issue #256）；
- 行为不回归：CLOSED 态下事件序列与接线前一致（由既有 executor 测试基线承担）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.planner import Plan, PlanStep
from app.agent.retry_policy import get_policy
from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_state_machine_api_semantics():
    b = CircuitBreaker(name="t", failure_threshold=3, recovery_timeout=30)
    assert b.allow_request() is True
    assert b.state is CircuitState.CLOSED

    b.record_failure()
    b.record_failure()
    assert b.state is CircuitState.CLOSED, "未达阈值不熔断"
    b.record_failure()
    assert b.state is CircuitState.OPEN, "达阈值转 OPEN"
    assert b.allow_request() is False, "OPEN 且窗口未过 → 拒绝"


def test_half_open_recovery_cycle():
    import time as _time

    b = CircuitBreaker(name="t2", failure_threshold=1, recovery_timeout=0)
    b.record_failure()
    assert b.state is CircuitState.OPEN
    b.last_failure_time = _time.time() - 1  # 拨回 1s：窗口必已过（Windows 时钟粒度下更稳）

    assert b.allow_request() is True, "恢复窗口过后应放行半开探测"
    assert b.state is CircuitState.HALF_OPEN
    b.record_success()
    assert b.state is CircuitState.CLOSED, "探测成功 → 闭合（防'永远熔断'回归）"


def test_circuit_open_error_is_classifiable():
    assert CircuitOpenError("x").error_type == "circuit_open"
    assert get_policy("circuit_open").retryable is False, "OPEN 期内重试只是白付探测成本"


def test_production_breaker_constants_pinned():
    """生产常量钉（issue #256 minor-4）：_LLM_BREAKER 的阈值/恢复窗口是运行语义
    的一部分（executor.py 模块注释与 README 声称 3 次失败 / 30s 半开）。
    无声改参 → 本用例红。"""
    import app.agent.executor as ex_mod

    assert ex_mod._LLM_BREAKER.failure_threshold == 3
    assert ex_mod._LLM_BREAKER.recovery_timeout == 30


@pytest.mark.asyncio
async def test_executor_fast_fails_when_breaker_open(monkeypatch):
    """判别性核心：达阈值后，下一次调用**不再触达 client**（call_count 不增），
    且事件为 tool_error(error_type="circuit_open")。

    变异点：删 executor 的 allow_request 门 → 第 4 次仍触达 client → 本用例红。
    """
    import app.agent.executor as ex_mod

    breaker = CircuitBreaker(name="llm-test", failure_threshold=3, recovery_timeout=60)
    monkeypatch.setattr(ex_mod, "_LLM_BREAKER", breaker)

    client = MagicMock()
    calls = {"n": 0}

    async def _fail(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        raise ConnectionError("provider down")

    client.chat.completions.create = _fail

    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(
        client,
        AgentConfig(timeout=0.5, enable_planning=False, enable_reflection=False),
        memory,
    )
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])

    async def _run_once():
        return [e async for e in ex._execute_step_once(
            step=step, plan=plan, history=None, organization_id=1, user_id=0,
            enable_thinking=False,
        )]

    for _ in range(3):
        events = await _run_once()
        assert any(e.type == "tool_error" for e in events)

    assert breaker.state is CircuitState.OPEN
    assert calls["n"] == 3, "三次失败各触达 client 一次（前置条件）"

    events = await _run_once()
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[-1].error_type == "circuit_open", "OPEN 时必须给出可分类错误码"
    assert calls["n"] == 3, "OPEN 后不得再触达 client（判别点：接线前会继续打并等超时）"


@pytest.mark.asyncio
async def test_blocked_requests_do_not_record_failure(monkeypatch):
    """门被拦的请求不得记账（issue #256 守护；复核变异 M6 的判别缺口）。

    门路径若也调 record_failure：每次被拦都刷新 last_failure_time
    （_handle_failure 无条件刷新）→ OPEN 窗口自延长，持续流量下永不恢复。
    判别变异：门路径加 `_LLM_BREAKER.record_failure()` → 本用例红。
    """
    import app.agent.executor as ex_mod

    breaker = CircuitBreaker(name="llm-guard", failure_threshold=1, recovery_timeout=60)
    monkeypatch.setattr(ex_mod, "_LLM_BREAKER", breaker)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    recorded = {"n": 0}
    real_record_failure = breaker.record_failure

    def _spy_record_failure():
        recorded["n"] += 1
        real_record_failure()

    monkeypatch.setattr(breaker, "record_failure", _spy_record_failure)
    window_before = breaker.last_failure_time

    client_calls = {"n": 0}

    async def _client(*, messages, tools=None, **kwargs):
        client_calls["n"] += 1
        raise ConnectionError("blocked request must not reach the client")

    client = MagicMock()
    client.chat.completions.create = _client

    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(
        client,
        AgentConfig(timeout=0.5, enable_planning=False, enable_reflection=False),
        memory,
    )
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])

    for _ in range(5):
        events = [e async for e in ex._execute_step_once(
            step=step, plan=plan, history=None, organization_id=1, user_id=0,
            enable_thinking=False,
        )]
        errs = [e for e in events if e.type == "tool_error"]
        assert errs and errs[-1].error_type == "circuit_open"  # 前提：请求确实被门拦下

    assert client_calls["n"] == 0, "被拦请求不得触达 client（前提）"
    assert recorded["n"] == 0, "门被拦的请求不得调用 record_failure（M6 守护）"
    assert breaker.last_failure_time == window_before, (
        "被拦请求不得刷新失败时间戳（否则窗口自延长、永不恢复）"
    )


@pytest.mark.asyncio
async def test_gate_fast_fail_ends_observability_spans(monkeypatch):
    """门早退也必须收尾 span（issue #256）：step_span / llm_span 在门之前都已创建，
    被拦时不得悬挂（观测层数据缺失）。

    判别变异：删掉门路径的两个 end() → 本用例红。
    """
    import app.agent.executor as ex_mod
    import app.agent.observability as obs_mod

    breaker = CircuitBreaker(name="llm-span", failure_threshold=1, recovery_timeout=60)
    monkeypatch.setattr(ex_mod, "_LLM_BREAKER", breaker)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    fake_lf = MagicMock()
    step_span = MagicMock()
    llm_span = MagicMock()
    fake_lf.span.return_value = step_span
    fake_lf.generation.return_value = llm_span
    monkeypatch.setattr(obs_mod, "get_langfuse", lambda: fake_lf)

    client = MagicMock()
    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(
        client,
        AgentConfig(timeout=0.5, enable_planning=False, enable_reflection=False),
        memory,
    )
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [e async for e in ex._execute_step_once(
        step=step, plan=plan, history=None, organization_id=1, user_id=0,
        enable_thinking=False,
    )]

    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[-1].error_type == "circuit_open"  # 前提：门确实拦下了
    assert step_span.end.called, "门早退必须收尾 step_span（否则悬挂）"
    assert llm_span.end.called, "门早退必须收尾 llm_span（否则悬挂）"
    assert "circuit_open" in str(step_span.end.call_args.kwargs.get("output", ""))


@pytest.mark.asyncio
async def test_executor_breaker_recovers_after_successful_probe(monkeypatch):
    """OPEN + 窗口过后：探测调用触达 client 且成功 → 熔断闭合、后续正常。"""
    import app.agent.executor as ex_mod

    breaker = CircuitBreaker(name="llm-recover", failure_threshold=1, recovery_timeout=0)
    monkeypatch.setattr(ex_mod, "_LLM_BREAKER", breaker)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    breaker.last_failure_time -= 1  # 窗口必已过（Windows 时钟粒度下更稳）

    client = MagicMock()

    async def _ok(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        msg = MagicMock()
        msg.content = "ok"
        msg.tool_calls = None
        resp.choices = [MagicMock()]
        resp.choices[0].message = msg
        resp.usage = None
        return resp

    client.chat.completions.create = _ok

    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(client, AgentConfig(timeout=2.0, enable_planning=False, enable_reflection=False), memory)
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])

    _events = [e async for e in ex._execute_step_once(
        step=step, plan=plan, history=None, organization_id=1, user_id=0,
        enable_thinking=False,
    )]

    assert breaker.state is CircuitState.CLOSED, "半开探测成功 → 闭合"
