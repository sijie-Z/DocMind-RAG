"""issue #106 第 5 项：Evaluation 三器的输出 actuate 回归测试。

背景（#106 §5）：`replan` 判定算出来就被丢掉（loop 唯一分支只认 retry）、
`fatal_issues` 只变成一条 thinking、`reviewer.recommendation` 全仓零读取者。
这里对三处各写一组**有牙**的行为测试：

- 5a：reflection 判 replan → planner 真的被再次调用（带反思反馈），并用新计划执行；
      反复 replan → 在上限处停止，不会无限循环；pass/retry 不触发额外规划。
- 5b：fatal_issues 非空 → 结构化信号（决策 + run report 字段）显示本次 run
      不是一次干净的成功；clean 时不产生噪音。
- 5c：reject → 可观测的结构化后果；revise → 被记录；pass → 无噪音。

测试用假件（Fake planner/executor/reflector/reviewer/memory）驱动完整 run()，
断言的是**外在可观察的调用与事件**，不是实现细节。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.executor import Executor
from app.agent.loop import MAX_REPLANS_PER_RUN, PERAgentLoop
from app.agent.planner import Plan

# ── 假件 ────────────────────────────────────────────────────────────────────


class FakeMemory:
    """替身工作记忆：store_step_result 写入的内容就是质检/反思读到的内容。"""

    def __init__(self):
        self.results: dict = {}
        self.interactions: list[tuple[str, str]] = []
        self.reset_count = 0

    def reset_working(self) -> None:
        self.reset_count += 1
        self.results = {}

    def get_all_results(self) -> dict:
        return dict(self.results)

    def store_step_result(self, step_id, result) -> None:
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_interaction(self, user_input, assistant_response) -> None:
        self.interactions.append((user_input, assistant_response))

    async def record_experience(self, **kwargs) -> None:
        pass


class RecordingPlanner:
    """每次调用生成一版可区分的新计划，并记录收到的 replan_feedback。"""

    # 防御性上限：如果 replan 失去有界性，这里会快速报错而不是把测试跑挂。
    SAFETY_CALL_CAP = 5

    def __init__(self):
        self.calls: list[dict] = []

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls.append({"query": query, "replan_feedback": replan_feedback})
        if len(self.calls) > self.SAFETY_CALL_CAP:
            raise AssertionError("planner 调用次数失控 —— replan 没有被有界地限制")
        n = len(self.calls)
        plan_id = f"plan-{n}"
        yield AgentEvent(type="plan_start", plan_id=plan_id, content="制定计划...")
        yield AgentEvent(
            type="plan_step",
            plan_id=plan_id,
            plan_step_id="s1",
            content=f"第{n}版步骤",
            tool_hint="search_knowledge_base",
        )
        yield AgentEvent(
            type="plan_complete", plan_id=plan_id, plan_goal=f"目标-{n}", content="计划完成"
        )


class RecordingExecutor(Executor):
    """记录被执行的 plan；反思重试复用真实 Executor 的正常步骤收尾。"""

    def __init__(self, memory: FakeMemory, result_text: str = "这是执行产出的完整结果内容。"):
        super().__init__(
            MagicMock(),
            AgentConfig(
                enable_memory=False,
                enable_thinking=False,
                enable_skills=False,
                enable_experience=False,
            ),
            memory,
        )
        self.result_text = result_text
        self.executed_plans: list[Plan] = []
        self.retry_calls = 0

    def reset_for_request(self) -> None:
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
        self.executed_plans.append(plan)
        for s in plan.steps:
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(
                s.id,
                {
                    "description": s.description,
                    "status": "completed",
                    "result": self.result_text,
                    "tool_hint": s.tool_hint,
                },
            )
        yield AgentEvent(type="chunk", content=f"答案-{plan.id}")

    async def _execute_step_with_retry(
        self, step, plan, history, organization_id, user_id, enable_thinking, ctx=None
    ):
        self.retry_calls += 1
        yield AgentEvent(type="chunk", content=f"重试产出-{step.id}")


class ScriptedReflector:
    """按脚本播报反思判定；脚本耗尽后重复最后一条（模拟持续投 replan）。"""

    def __init__(self, decisions: list[str]):
        self.decisions = list(decisions)
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        step_id = "s1" if decision == "retry" else ""
        # 与真实 Reflector 一致：判定事件（带 reasoning）+ 摘要事件（缺口）。
        yield AgentEvent(
            type="reflection",
            reflection_result=decision,
            content="评估理由：计划覆盖不足",
            plan_id=plan.id,
            plan_step_id=step_id,
        )
        yield AgentEvent(
            type="reflection",
            reflection_result=decision,
            content="完成度 40\n发现的缺口:\n- 缺少对比数据",
            plan_id=plan.id,
        )


class StubReviewer:
    def __init__(self, result: dict | None = None):
        self.result = (
            result if result is not None else {"issues_found": [], "recommendation": "pass"}
        )
        self.calls = 0

    async def review(self, plan, results, ctx=None):
        self.calls += 1
        return self.result


# ── 驱动工具 ────────────────────────────────────────────────────────────────


def make_loop(reflector, reviewer=None, result_text="这是执行产出的完整结果内容。"):
    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False),
        organization_id=1,
        user_id=0,
    )
    memory = FakeMemory()
    loop.memory = memory
    loop._executor = RecordingExecutor(memory, result_text=result_text)
    loop._planner = RecordingPlanner()
    loop._reflector = reflector
    loop._reviewer = reviewer or StubReviewer()
    # 经验回写另有测试覆盖（test_issue106_wiring）；这里隔离掉，避免测试相互干扰。
    loop._write_experiences = AsyncMock()
    return loop


async def drive(loop, query="对比两份财报", timeout=15.0):
    async def _collect():
        return [e async for e in loop.run(query)]

    return await asyncio.wait_for(_collect(), timeout=timeout)


def decisions_of(events) -> list[dict]:
    ctx_events = [e for e in events if e.type == "execution_context"]
    assert ctx_events, "run() 必须发出 execution_context 事件"
    return ctx_events[-1].content["decisions"]


def run_report_text(events) -> str:
    reports = [e for e in events if e.type == "run_report"]
    assert reports, "run() 必须发出 run_report 事件"
    return reports[-1].content


@pytest.fixture(autouse=True)
def _no_ctx_persistence(monkeypatch):
    """ExecutionContext.save() 在测试里不应写 Redis / benchmark 目录。"""
    from app.agent.exec_context import ExecutionContext

    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)


# ── 5a：replan 真的触发重新规划（有界） ─────────────────────────────────────


class TestReplanActuation:
    @pytest.mark.asyncio
    async def test_replan_triggers_replanning_with_feedback_and_new_plan(self):
        loop = make_loop(ScriptedReflector(["replan", "pass"]))
        events = await drive(loop)

        planner = loop._planner
        executor = loop._executor

        # 1) planner 被再次调用（初始 1 次 + replan 1 次），且收到了反思反馈。
        assert len(planner.calls) == 2, (
            f"replan 应触发一次重新规划，实际 planner 调用 {len(planner.calls)} 次"
        )
        feedback = planner.calls[1]["replan_feedback"]
        assert "缺口" in feedback and "缺少对比数据" in feedback, (
            f"反思给出的缺口/质量问题应作为反馈传给 planner，实际: {feedback!r}"
        )

        # 2) 执行的是新计划（第二版），不是旧计划重跑。
        assert len(executor.executed_plans) == 2
        assert executor.executed_plans[0].steps[0].description == "第1版步骤"
        assert executor.executed_plans[1].steps[0].description == "第2版步骤"

        # 3) 事件流里有明确的“正在重新规划”信号（SSE 可观测）。
        assert any(
            e.type == "thinking" and "replan" in e.content and "重新规划" in e.content
            for e in events
        ), "重新规划必须发出明确可观测的 thinking 事件"

        # 4) 用户答案/记忆里记录的是新计划的产出，旧产出被取代。
        assert loop.memory.interactions[-1][1] == "答案-plan-2"

    @pytest.mark.asyncio
    async def test_repeated_replan_stops_at_the_bound(self):
        # 反思永远说 replan —— 必须有界，否则死循环。
        loop = make_loop(ScriptedReflector(["replan"]))
        events = await drive(loop)

        expected = 1 + MAX_REPLANS_PER_RUN
        assert len(loop._planner.calls) == expected, (
            f"反复 replan 时 planner 应被调用 {expected} 次（初始 1 次 + 上限内 "
            f"{MAX_REPLANS_PER_RUN} 次），实际 {len(loop._planner.calls)} 次"
        )
        assert len(loop._executor.executed_plans) == expected
        assert any(e.type == "done" for e in events), "有界停止后 run 必须正常收尾"
        assert any(e.type == "thinking" and "已达上限" in e.content for e in events), (
            "达到上限时应发出可观测提示"
        )
        # 隔离措施：run 开始 + 每次 replan 前各一次。
        assert loop.memory.reset_count == expected

    @pytest.mark.asyncio
    async def test_pass_does_not_replan(self):
        loop = make_loop(ScriptedReflector(["pass"]))
        events = await drive(loop)

        assert len(loop._planner.calls) == 1, "pass 不应触发任何额外规划"
        assert loop._planner.calls[0]["replan_feedback"] == ""
        assert not any(e.type == "thinking" and "重新规划" in e.content for e in events), (
            "pass 路径不应出现重新规划噪音"
        )

    @pytest.mark.asyncio
    async def test_retry_does_not_replan_and_keeps_existing_behavior(self):
        loop = make_loop(ScriptedReflector(["retry"]))
        events = await drive(loop)

        assert len(loop._planner.calls) == 1, "retry 不应触发额外规划"
        assert loop._executor.retry_calls == 1, "retry 路径应恰好重试一次步骤"
        # retry 的既有语义不变：重试产出追加进最终答案。
        assert loop.memory.interactions[-1][1] == "答案-plan-1\n\n[重试结果]\n重试产出-s1"
        assert not any(e.type == "thinking" and "重新规划" in e.content for e in events)


# ── 5b：fatal_issues 的诚实 actuation ───────────────────────────────────────


class TestFatalIssuesActuation:
    @pytest.mark.asyncio
    async def test_fatal_issues_produce_structured_not_clean_signal(self):
        loop = make_loop(
            ScriptedReflector(["pass"]),
            result_text="Error: connection refused while calling tool",
        )
        events = await drive(loop)

        # 结构化决策记录（随 execution_context 事件输出）。
        assert any(
            d["phase"] == "quality_gate" and d["action"] == "fatal_issues"
            for d in decisions_of(events)
        ), "fatal_issues 必须有结构化的决策记录，不能只有 thinking 文本"

        # run report 明确标记：这不是一次干净的成功。
        text = run_report_text(events)
        assert "clean_success: False" in text
        assert "FATAL:" in text
        assert "Error: connection refused" in text

    @pytest.mark.asyncio
    async def test_clean_run_keeps_clean_success_true_and_no_noise(self):
        loop = make_loop(ScriptedReflector(["pass"]))
        events = await drive(loop)

        text = run_report_text(events)
        assert "clean_success: True" in text
        assert "FATAL:" not in text
        assert not any(d["phase"] == "quality_gate" for d in decisions_of(events)), (
            "质检通过时不应产生 fatal 决策噪音"
        )


# ── 5c：reviewer.recommendation 的消费 ──────────────────────────────────────


class TestReviewRecommendationActuation:
    @pytest.mark.asyncio
    async def test_reject_is_consumed_structurally(self):
        reviewer = StubReviewer(
            {
                "overall_assessment": "不可信",
                "issues_found": [],
                "key_strengths": [],
                "recommendation": "reject",
            }
        )
        loop = make_loop(ScriptedReflector(["pass"]), reviewer=reviewer)
        events = await drive(loop)

        assert any(
            d["phase"] == "review" and d["action"] == "reject" for d in decisions_of(events)
        ), "reject 必须有结构化的决策记录"
        text = run_report_text(events)
        assert "review_recommendation: reject" in text
        assert "clean_success: False" in text, "reject 的 run 不得被呈现为干净成功"

        # 用户可见的答案正文不被改动：chunk 只含执行产出的原文。
        chunks = "".join(e.content for e in events if e.type == "chunk")
        assert chunks == "答案-plan-1"

    @pytest.mark.asyncio
    async def test_revise_is_recorded(self):
        reviewer = StubReviewer({"issues_found": [], "recommendation": "revise"})
        loop = make_loop(ScriptedReflector(["pass"]), reviewer=reviewer)
        events = await drive(loop)

        assert any(
            d["phase"] == "review" and d["action"] == "revise" for d in decisions_of(events)
        ), "revise 至少要被记录（结构化决策）"
        text = run_report_text(events)
        assert "review_recommendation: revise" in text
        # 选定语义：revise 与质检 C 档同级 —— 只记录/暴露，不翻转 clean_success。
        assert "clean_success: True" in text

    @pytest.mark.asyncio
    async def test_pass_recommendation_is_silent(self):
        loop = make_loop(ScriptedReflector(["pass"]))
        events = await drive(loop)

        assert not any(d["phase"] == "review" for d in decisions_of(events)), (
            "pass 不应产生 review 决策噪音"
        )
        assert not any(
            e.type == "thinking" and ("reject" in e.content or "revise" in e.content)
            for e in events
        )
        # 但 run report 仍然如实暴露审查结论。
        assert "review_recommendation: pass" in run_report_text(events)
