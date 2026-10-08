"""issue #242 第 7 条：circuit_breaker 状态机 API 接入 LLM 步骤路径的判别性测试。

- 状态机 API 语义（CLOSED / OPEN / HALF_OPEN、allow_request / record_*）；
- executor 判别性：连续失败达阈值 → OPEN → 下一次调用**不再触达 client**
  （毫秒级快速失败；错误码 circuit_open；retry_policy 为 fatal）；
- 恢复路径（状态机层）：OPEN + 恢复窗口过后半开探测成功 → CLOSED；
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
