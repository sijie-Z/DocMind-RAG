"""issue #106 §4 的可靠性接线回归测试。

§4a：串行模式下连续失败熔断与低增益早停因 `continue` 而不可达 —— 两条保护
    在 serial 模式必须真正生效，且 DAG 模式的现有行为保持不变。
§4b：生产路径的 LLM 调用缺少 asyncio 级硬边界；StepRunner（max_retries=0）
    补上边界后，失败必须携带 error_type 走执行器既有的分类/重试路径，
    不许变成静默失败。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.retry_policy import get_policy


def _memory() -> AgentMemoryBridge:
    return AgentMemoryBridge(agent_id="test-106-reliability", organization_id=1, user_id=0)


class _ScriptedExecutor(Executor):
    """确定性步骤执行器：`_execute_step_once` 是 async generator，直接替换它，
    而不是去 mock LLM 客户端。"""

    def __init__(self, *args, outcome="fail", **kwargs):
        super().__init__(*args, **kwargs)
        self.outcome = outcome
        self.executed: list[str] = []

    async def _execute_step_once(
        self,
        step,
        plan,
        history,
        organization_id,
        user_id,
        enable_thinking,
        error_context="",
        ctx=None,
    ):
        self.executed.append(step.id)
        if self.outcome == "fail":
            # permission_denied 是 fatal 级错误 → 不触发步骤内重试，一次到位
            yield AgentEvent(
                type="tool_error",
                tool_name="fake_tool",
                content="boom",
                error_type="permission_denied",
                plan_id=plan.id,
                plan_step_id=step.id,
            )
        else:
            # 每次返回完全相同的结论 → 信息增益为 0
            yield AgentEvent(
                type="chunk",
                content="同一份结论，没有新增信息",
                plan_id=plan.id,
                plan_step_id=step.id,
            )


async def _run(executor: Executor, plan: Plan) -> list[AgentEvent]:
    return [e async for e in executor.execute(plan, organization_id=1)]


# ── §4a：串行模式的两道保护 ─────────────────────────────────────────────


class TestSerialModeGuards:
    """两道保护此前在 serial 模式下永远不可达（executor.py 的 `continue` 跳过）。"""

    @pytest.mark.asyncio
    async def test_consecutive_failures_skip_remaining_steps_in_serial_mode(self):
        executor = _ScriptedExecutor(
            MagicMock(),
            AgentConfig(),
            _memory(),
            outcome="fail",
            execution_mode="serial",
        )
        plan = Plan(
            id="p1",
            goal="g",
            steps=[PlanStep(id=f"s{i}", description=f"步骤 {i}") for i in range(1, 6)],
        )

        events = await _run(executor, plan)

        executed = executor.executed
        assert executed == ["s1", "s2", "s3"], (
            f"连续失败达到 MAX_CONSECUTIVE_FAILURES=3 后不应再执行步骤，实际执行了 {executed}"
        )
        assert [s.status for s in plan.steps] == [
            "failed",
            "failed",
            "failed",
            "skipped",
            "skipped",
        ], "剩余 pending 步骤应被熔断标记为 skipped"
        assert any(
            e.type == "thinking"
            and "连续 3 次步骤失败" in e.content
            and "剩余步骤已跳过" in e.content
            for e in events
        ), "熔断触发时必须产出 thinking 事件（静默跳过不可接受）"
        assert plan.is_complete

    @pytest.mark.asyncio
    async def test_low_gain_streak_stops_serial_execution(self):
        executor = _ScriptedExecutor(
            MagicMock(),
            AgentConfig(),
            _memory(),
            outcome="passthrough",
            execution_mode="serial",
        )
        plan = Plan(
            id="p1",
            goal="g",
            steps=[PlanStep(id=f"s{i}", description=f"步骤 {i}") for i in range(1, 5)],
        )

        await _run(executor, plan)

        executed = executor.executed
        assert executed == ["s1", "s2", "s3"], (
            f"连续 {executor.MAX_LOW_GAIN_STEPS} 个低增益步骤后应早停，实际执行了 {executed}"
        )
        assert [s.status for s in plan.steps] == [
            "completed",
            "completed",
            "completed",
            "skipped",
        ], "低增益早停后剩余 pending 步骤应被标记为 skipped"
        assert plan.is_complete

    @pytest.mark.asyncio
    async def test_dag_mode_breaker_still_fires(self):
        """4a 只移动检查位置：DAG 模式下熔断行为保持原样（回归钉）。"""
        executor = _ScriptedExecutor(MagicMock(), AgentConfig(), _memory())  # 默认 dag
        executor._consecutive_failures = executor.MAX_CONSECUTIVE_FAILURES
        plan = Plan(
            id="p1",
            goal="g",
            steps=[PlanStep(id=f"s{i}", description=f"步骤 {i}") for i in range(1, 4)],
        )

        events = await _run(executor, plan)

        assert executor.executed == [], "熔断已打开时不应再执行任何步骤"
        assert all(s.status == "skipped" for s in plan.steps)
        assert any(e.type == "thinking" and "剩余步骤已跳过" in e.content for e in events)
        assert plan.is_complete

    @pytest.mark.asyncio
    async def test_dag_mode_low_gain_still_fires(self):
        executor = _ScriptedExecutor(MagicMock(), AgentConfig(), _memory())  # 默认 dag
        executor._consecutive_low_gain = executor.MAX_LOW_GAIN_STEPS
        plan = Plan(
            id="p1",
            goal="g",
            steps=[PlanStep(id=f"s{i}", description=f"步骤 {i}") for i in range(1, 4)],
        )

        await _run(executor, plan)

        assert executor.executed == [], "低增益早停已触发时不应再执行任何步骤"
        assert all(s.status == "skipped" for s in plan.steps)
        assert plan.is_complete


# ── §4b：LLM 调用的硬超时 + 失败可见性 ───────────────────────────────────


def _executor_with_client(client: MagicMock, cfg: AgentConfig) -> Executor:
    executor = Executor(client, cfg, _memory())
    # 记忆召回与本节主题无关，且它的耗时随环境浮动（Redis / embedding 尝试
    # 可达数秒）。stub 掉之后这些测试只度量 LLM 调用边界的时序。
    executor.memory.get_context_for_query = AsyncMock(return_value="")
    return executor


class TestLLMCallHardTimeout:
    """StepRunner 只借 timeout 与 StepResult，max_retries=0（重试仍归执行器）。"""

    @staticmethod
    def _hanging_client(seconds: float) -> MagicMock:
        async def _hanging_create(**kwargs):
            await asyncio.sleep(seconds)

        client = MagicMock()
        client.chat.completions.create = _hanging_create
        return client

    @pytest.mark.asyncio
    async def test_hung_llm_call_surfaces_a_classified_timeout(self, monkeypatch):
        """超过硬边界的调用 → 带 error_type 的 tool_error（不挂死、不静默成功）。"""
        monkeypatch.setattr(Executor, "LLM_HARD_TIMEOUT_MARGIN_SECONDS", 0.0)
        executor = _executor_with_client(self._hanging_client(10), AgentConfig(timeout=0.05))
        step = PlanStep(id="s1", description="测试步骤")
        plan = Plan(id="p1", goal="g", steps=[step])

        async def _collect():
            return [
                e
                async for e in executor._execute_step_once(
                    step=step,
                    plan=plan,
                    history=None,
                    organization_id=1,
                    user_id=0,
                    enable_thinking=True,
                )
            ]

        # 预算 5s 只是「挂了就红」的护栏；正常路径 <1s 返回（含一次性初始化开销）
        events = await asyncio.wait_for(_collect(), timeout=5)

        errors = [e for e in events if e.type == "tool_error"]
        assert errors, "挂死的 LLM 调用必须浮出失败，而不是挂死或静默成功"
        assert errors[0].error_type == "timeout", (
            f"应被分类为 timeout，实际 error_type={errors[0].error_type!r}"
        )
        assert not any(e.type == "chunk" for e in events), "超时不得伪装成正常输出"
        assert get_policy(errors[0].error_type).retryable is True, (
            "分类结果必须能被既有重试策略消费"
        )

    @pytest.mark.asyncio
    async def test_timeout_error_reaches_the_existing_retry_loop(self, monkeypatch):
        """error_type 出现在事件流上后，执行器自己的重试循环能够看到并重试。"""
        monkeypatch.setattr(Executor, "LLM_HARD_TIMEOUT_MARGIN_SECONDS", 0.0)
        cfg = AgentConfig(timeout=0.05, max_retries_per_step=1)
        executor = _executor_with_client(self._hanging_client(10), cfg)
        step = PlanStep(id="s1", description="测试步骤")
        plan = Plan(id="p1", goal="g", steps=[step])

        async def _collect():
            return [
                e
                async for e in executor._execute_step_with_retry(
                    step=step,
                    plan=plan,
                    history=None,
                    organization_id=1,
                    user_id=0,
                    enable_thinking=True,
                )
            ]

        events = await asyncio.wait_for(_collect(), timeout=5)

        retry_events = [e for e in events if e.type == "thinking" and "重试步骤" in e.content]
        assert len(retry_events) == 1, f"应恰好重试一次，实际 {len(retry_events)} 次"
        assert "timeout" in retry_events[0].content, "重试决策应基于被分类的 timeout"
        assert step.status == "failed"
        assert any(e.type == "tool_error" and "步骤失败" in e.content for e in events)

    @pytest.mark.asyncio
    async def test_normal_llm_call_event_sequence_is_unchanged(self):
        """正常调用：结果与事件序列和接线前一致（单个 chunk，无附加事件）。"""
        resp = MagicMock()
        resp.choices[0].message.content = "你好，这是正常回答"
        resp.choices[0].message.tool_calls = None

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=resp)

        executor = _executor_with_client(client, AgentConfig(timeout=5.0))
        step = PlanStep(id="s1", description="测试步骤")
        plan = Plan(id="p1", goal="g", steps=[step])

        events = [
            e
            async for e in executor._execute_step_once(
                step=step,
                plan=plan,
                history=None,
                organization_id=1,
                user_id=0,
                enable_thinking=True,
            )
        ]

        assert [e.type for e in events] == ["chunk"]
        assert events[0].content == "你好，这是正常回答"
