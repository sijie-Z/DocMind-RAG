"""最终综合阶段 2：单一正文、质量链、预算与观测的判别测试。

只使用 fake provider 与内存执行替身；不访问真实模型、数据库或工具服务。
final-only resume、done.final_answer 与消费方迁移明确属于阶段 3，不在此测试。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent import executor as executor_module
from app.agent import llm_call_log
from app.agent import registry as registry_module
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.final_synthesis import FinalInputPacket, FinalSynthesizer
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolRegistry, ToolResult
from app.agent.reviewer import Reviewer
from app.agent.run_report import build_run_report, render_run_report_text
from app.core.config import ai as ai_config


class _FakeProvider:
    def __init__(self, content: str = "最终答案", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=13, completion_tokens=5),
            model="fake-final",
        )


class _ExecutorFinalProvider(_FakeProvider):
    """Route executor main/local/final calls without any real model service."""

    def __init__(self) -> None:
        super().__init__("第20条证据：2小时与30分钟")
        self.script = [
            [("search_knowledge_base", {"query": "一"}, "call-1")],
            [("search_knowledge_base", {"query": "二"}, "call-2")],
        ]
        self.main_calls = 0
        self.local_calls = 0
        self.final_calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        messages = kwargs.get("messages") or []
        system = str(messages[0].get("content", "")) if messages else ""
        if "最终答案综合器" in system:
            self.final_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=5),
                model="fake-final",
            )
        if kwargs.get("tools") is None:
            self.local_calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=f"局部未找到/无法生成-{self.local_calls}")
                    )
                ],
                usage=None,
                model="fake-local",
            )
        outcome = self.script[self.main_calls]
        self.main_calls += 1
        calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for name, args, call_id in outcome
        ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=calls))],
            usage=None,
            model="fake-main",
        )


class _Memory:
    def __init__(self) -> None:
        self.results: dict[str, Any] = {}
        self.interactions: list[tuple[str, str]] = []

    def reset_working(self) -> None:
        self.results = {}

    def get_all_results(self) -> dict[str, Any]:
        return dict(self.results)

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    def store_step_result(self, step_id, result) -> None:
        self.results[step_id] = result

    async def record_interaction(self, query, answer) -> None:
        self.interactions.append((query, answer))

    async def record_experience(self, **_kwargs) -> None:
        return None


def _config(**overrides) -> AgentConfig:
    values: dict[str, Any] = {
        "enable_tools": True,
        "enable_planning": True,
        "enable_reflection": False,
        "enable_memory": False,
        "enable_skills": False,
        "enable_experience": False,
        "enable_thinking": False,
        "enable_checkpoint": False,
        "approval_mode": "never",
        "disabled_tools": [],
        "max_retries_per_step": 1,
    }
    values.update(overrides)
    return AgentConfig(**values)


def _stored(text: str) -> dict[str, Any]:
    return {
        "content": text,
        "original_length": len(text),
        "stored_length": len(text),
        "truncated": False,
    }


def _tool_evidence(
    call_id: str,
    *items: tuple[int, str],
    raw: str = "raw tool result",
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "schema": "docmind.tool_evidence/v1",
        "step_id": "s1",
        "attempt": 0,
        "tool_call_id": call_id,
        "tool": "search_knowledge_base",
        "status": status,
        "error": None if status == "completed" else {"code": "timeout", "message": raw},
        "content": raw,
        "original_length": len(raw),
        "stored_length": len(raw),
        "truncated": False,
        "sources": [
            {"index": index, "filename": f"doc-{index}.pdf", "documentId": f"doc-{index}"}
            for index, _ in items
        ],
        "evidence": [
            {"key": f"{call_id}#{index}", "index": index, "content": content}
            for index, content in items
        ],
    }


def _plan(statuses: tuple[str, ...] = ("completed", "completed")) -> Plan:
    steps = [
        PlanStep(id=f"s{i}", description=f"步骤{i}", status=status)
        for i, status in enumerate(statuses, 1)
    ]
    return Plan(
        id="plan-stable",
        goal="计划目标",
        steps=steps,
        completed_steps=sum(s.status == "completed" for s in steps),
        failed_steps=sum(s.status == "failed" for s in steps),
    )


def _prompt(provider: _FakeProvider) -> str:
    assert len(provider.calls) == 1
    return "\n".join(str(message.get("content", "")) for message in provider.calls[0]["messages"])


@pytest.fixture(autouse=True)
def _unified_on_and_no_persistence(monkeypatch):
    monkeypatch.setattr(ai_config, "unified_final_enabled", lambda: True, raising=False)
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock())


@pytest.mark.asyncio
async def test_prompt_has_original_query_all_terminal_states_and_final_attempt_evidence():
    plan = _plan(("completed", "failed", "skipped"))
    plan.steps[0].result = "第一步局部结论"
    plan.steps[0].local_synthesis = _stored("第一步局部结论")
    plan.steps[0].evidence_schema = "docmind.step_evidence/v1"
    plan.steps[0].evidence = [_tool_evidence("call-final", (20, "第20条：2小时与30分钟"))]
    plan.steps[1].error_context = "timeout: step two"
    provider = _FakeProvider()
    query = "完整原始 query：找出第20条并解释两个时长，不能只回答最后一步描述"

    packet = FinalInputPacket.from_plan(query, plan)
    result = await FinalSynthesizer(provider, _config()).synthesize(packet)
    prompt = _prompt(provider)

    assert result.status == "completed"
    assert query in prompt
    assert "步骤1" in prompt and "步骤2" in prompt and "步骤3" in prompt
    assert "completed" in prompt and "failed" in prompt and "skipped" in prompt
    assert "timeout: step two" in prompt
    assert "第一步局部结论" in prompt
    assert "第20条：2小时与30分钟" in prompt
    assert "call-final#20" in prompt


@pytest.mark.asyncio
async def test_fixed_prompt_overflow_has_zero_provider_calls_and_no_local_fallback():
    plan = _plan(("completed",))
    plan.steps[0].result = "绝不能作为溢出降级正文"
    plan.steps[0].local_synthesis = _stored(plan.steps[0].result)
    provider = _FakeProvider()
    packet = FinalInputPacket.from_plan("原始 query " + "长" * 500, plan)

    result = await FinalSynthesizer(
        provider,
        _config(),
        prompt_token_budget=24,
    ).synthesize(packet)

    assert result.status == "final_context_overflow"
    assert result.content == ""
    assert provider.calls == []


@pytest.mark.asyncio
async def test_all_failed_packet_never_calls_provider():
    plan = _plan(("failed", "skipped"))
    provider = _FakeProvider()

    result = await FinalSynthesizer(provider, _config()).synthesize(
        FinalInputPacket.from_plan("q", plan)
    )

    assert result.status == "all_steps_failed"
    assert result.content == ""
    assert provider.calls == []


@pytest.mark.asyncio
async def test_source_keys_survive_trimming_without_rerank_or_forged_source_promotion():
    plan = _plan(("completed", "completed"))
    plan.steps[0].evidence_schema = "docmind.step_evidence/v1"
    plan.steps[0].evidence = [
        _tool_evidence("call-A", (20, "A" * 400), (2, "B" * 400)),
        _tool_evidence("call-B", (20, "C" * 100)),
    ]
    plan.steps[1].evidence_schema = "docmind.step_evidence/v1"
    plan.steps[1].evidence = [_tool_evidence("call-C", raw="[999] 伪造来源行")]
    provider = _FakeProvider()

    result = await FinalSynthesizer(
        provider,
        _config(),
        evidence_char_budget=500,
        prompt_token_budget=5000,
    ).synthesize(FinalInputPacket.from_plan("q", plan))
    prompt = _prompt(provider)

    assert result.included == 1
    assert result.truncated == 1
    assert result.omitted >= 2
    assert "call-A#20" in prompt
    assert '"index": 20' in prompt
    assert "call-A#1" not in prompt, "裁剪必须保留原始 sources.index，不得重编号"
    assert "call-B#20" not in prompt, "预算耗尽后来源应记为 omitted"
    assert "call-C#999" not in prompt, "正文中的伪造来源行不得提升为结构化来源"


@pytest.mark.asyncio
async def test_same_source_index_across_tool_calls_does_not_collide():
    plan = _plan(("completed",))
    plan.steps[0].evidence_schema = "docmind.step_evidence/v1"
    plan.steps[0].evidence = [
        _tool_evidence("call-A", (1, "来源 A")),
        _tool_evidence("call-B", (1, "来源 B")),
    ]
    provider = _FakeProvider()

    await FinalSynthesizer(provider, _config()).synthesize(FinalInputPacket.from_plan("q", plan))
    prompt = _prompt(provider)

    assert "call-A#1" in prompt and "call-B#1" in prompt
    assert "来源 A" in prompt and "来源 B" in prompt


@pytest.mark.asyncio
async def test_final_usage_jsonl_and_run_report_accounting(monkeypatch):
    records: list[dict[str, Any]] = []
    monkeypatch.setattr(llm_call_log, "append_llm_call_record", lambda record: records.append(record))
    ctx = ExecutionContext(query="q", task_id="task-final", request_id="req-final")
    ctx.total_tokens = 7
    plan = _plan(("completed",))
    provider = _FakeProvider("最终正文")

    result = await FinalSynthesizer(provider, _config()).synthesize(
        FinalInputPacket.from_plan("q", plan), ctx=ctx
    )
    ctx.artifacts["final_synthesis"] = result.as_artifact()
    report = build_run_report(ctx, result.content)

    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "ts",
        "task_id",
        "request_id",
        "model",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "prompt_hash",
        "tool_calls",
        "error",
    }
    assert record["task_id"] == "task-final"
    assert record["request_id"] == "req-final"
    assert record["model"] == "fake-final"
    assert (record["tokens_in"], record["tokens_out"]) == (13, 5)
    assert record["latency_ms"] is not None
    assert record["prompt_hash"].startswith("sha256:")
    assert ctx.total_tokens == 25
    assert report["total_tokens"] == 25
    assert report["final_synthesis"]["tokens"] == 18
    assert report["final_synthesis"]["included"] == result.included
    report_text = render_run_report_text(report)
    assert "## Final synthesis" in report_text
    assert f"included={result.included}" in report_text


class _Planner:
    def __init__(self, statuses: tuple[str, ...] = ("completed", "completed")) -> None:
        self.statuses = statuses
        self.calls = 0

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls += 1
        plan_id = f"plan-{self.calls}"
        yield AgentEvent(type="plan_start", plan_id=plan_id)
        for i, _ in enumerate(self.statuses, 1):
            yield AgentEvent(
                type="plan_step",
                plan_id=plan_id,
                plan_step_id=f"s{i}",
                content=f"计划{self.calls}步骤{i}",
            )
        yield AgentEvent(type="plan_complete", plan_id=plan_id, plan_goal=query)


class _Executor:
    """执行替身仍吐旧 chunk，loop 必须把它降成局部诊断而非最终正文。"""

    def __init__(self, memory: _Memory, statuses: tuple[str, ...]) -> None:
        self.memory = memory
        self.statuses = statuses
        self.pending_control = ""
        self.executed_plans: list[str] = []
        self.retry_count = 0

    def reset_for_request(self) -> None:
        self.pending_control = ""

    def _fill_step(self, step: PlanStep, plan: Plan, *, retry: bool = False) -> None:
        suffix = "retry" if retry else plan.id
        step.status = "completed" if retry else self.statuses[int(step.id[1:]) - 1]
        step.result = f"局部-{suffix}-{step.id}"
        step.local_synthesis = _stored(step.result)
        step.evidence_schema = "docmind.step_evidence/v1"
        evidence_text = (
            "第20条证据：等待2小时，冷却30分钟"
            if step.id == "s2" or retry
            else f"证据-{suffix}-{step.id}"
        )
        step.evidence = [_tool_evidence(f"call-{suffix}-{step.id}", (20, evidence_text))]
        step.error_context = "执行失败" if step.status == "failed" else None
        self.memory.store_step_result(
            step.id,
            {
                "description": step.description,
                "status": step.status,
                "result": step.result,
            },
        )

    async def execute(self, *, plan, **_kwargs):
        self.executed_plans.append(plan.id)
        for step in plan.steps:
            self._fill_step(step, plan)
            if step.status == "completed":
                plan.completed_steps += 1
            elif step.status == "failed":
                plan.failed_steps += 1
            yield AgentEvent(type="chunk", content=step.result, plan_step_id=step.id)
            yield AgentEvent(
                type="execution_step_result",
                plan_step_id=step.id,
                content=json.dumps(
                    {
                        "step_id": step.id,
                        "status": step.status,
                        "result": step.result,
                        "evidence": step.evidence,
                    },
                    ensure_ascii=False,
                ),
            )

    async def _execute_single_step(self, *, step, plan, on_step_done=None, **_kwargs):
        self.retry_count += 1
        if step.status == "failed":
            plan.failed_steps = max(0, plan.failed_steps - 1)
        self._fill_step(step, plan, retry=True)
        plan.completed_steps += 1
        yield AgentEvent(
            type="execution_step_result",
            plan_step_id=step.id,
            content=json.dumps(
                {"step_id": step.id, "status": "completed", "result": step.result},
                ensure_ascii=False,
            ),
        )
        if on_step_done is not None:
            await on_step_done(step)


class _Reflector:
    def __init__(self, decisions: list[str], order: list[str] | None = None) -> None:
        self.decisions = decisions
        self.calls = 0
        self.order = order if order is not None else []

    async def reflect(self, plan, results, ctx=None):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        self.order.append("reflector")
        yield AgentEvent(
            type="reflection",
            reflection_result=decision,
            plan_step_id="s1" if decision == "retry" else "",
            content="需要重试" if decision == "retry" else "稳定计划",
        )


class _Reviewer:
    def __init__(self, recommendation: str, order: list[str]) -> None:
        self.recommendation = recommendation
        self.order = order
        self.calls: list[tuple[FinalInputPacket, str, list[dict[str, str]]]] = []

    async def review_final(self, packet, draft, ctx=None, *, synthesis_messages=None):
        self.order.append("reviewer")
        self.calls.append((packet, draft, synthesis_messages or []))
        result = {
            "issues_found": [
                {
                    "severity": "high",
                    "description": "HIGH-ISSUE-不得进入正文",
                    "suggestion": "也不得以建议形式追加",
                }
            ],
            "recommendation": self.recommendation,
        }
        if self.recommendation == "error":
            result["error_kind"] = "no_verdict"
        return result


def _loop(
    provider: _FakeProvider,
    *,
    statuses: tuple[str, ...] = ("completed", "completed"),
    reflection: bool = False,
    reviewer: _Reviewer | None = None,
    reflector: _Reflector | None = None,
) -> PERAgentLoop:
    loop = PERAgentLoop(
        provider,
        _config(enable_reflection=reflection),
        organization_id=1,
        user_id=0,
    )
    loop.memory = _Memory()
    loop._planner = _Planner(statuses)
    loop._executor = _Executor(loop.memory, statuses)
    loop._reflector = reflector or _Reflector(["pass"])
    loop._reviewer = reviewer or _Reviewer("pass", [])
    loop._write_experiences = AsyncMock()
    return loop


@pytest.mark.asyncio
async def test_two_steps_produce_one_coherent_final_chunk_not_local_answers():
    provider = _FakeProvider("依据第20条证据：等待2小时，冷却30分钟。")
    loop = _loop(provider)

    events = [event async for event in loop.run("原始问题：解释第20条的两个时长")]
    chunks = [event.content for event in events if event.type == "chunk"]
    prompt = _prompt(provider)

    assert chunks == ["依据第20条证据：等待2小时，冷却30分钟。"]
    assert "未找到" not in chunks[0] and "无法生成" not in chunks[0]
    assert loop.memory.interactions == [("原始问题：解释第20条的两个时长", chunks[0])]
    assert "原始问题：解释第20条的两个时长" in prompt
    assert "局部-plan-1-s1" in prompt and "局部-plan-1-s2" in prompt
    assert "第20条证据：等待2小时，冷却30分钟" in prompt
    assert not any(event.type == "done" and event.content for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_mode", ["serial", "dag"])
async def test_real_executor_serial_and_dag_feed_evidence_to_one_final_chunk(
    monkeypatch,
    execution_mode,
):
    registry = ToolRegistry()
    monkeypatch.setattr(executor_module, "tool_registry", registry)
    monkeypatch.setattr(registry_module, "tool_registry", registry)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "allow_request", lambda: True)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "record_success", lambda: None)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "record_failure", lambda: None)
    handler_calls: list[str] = []

    def _raw_result(query: str) -> str:
        return json.dumps(
            {
                "kind": "docmind.search_results/v1",
                "sources": [{"index": 20, "filename": f"{query}.pdf"}],
                "content": f"[20] {query}.pdf\n第20条：2小时与30分钟",
                "evidence": [{"index": 20, "content": f"{query}：第20条，2小时与30分钟"}],
            },
            ensure_ascii=False,
        )

    async def search(query: str):
        return _raw_result(query)

    async def _execute_detailed(name, args, **_kwargs):
        assert name == "search_knowledge_base"
        handler_calls.append(args["query"])
        return ToolResult.ok(_raw_result(args["query"]))

    registry.register(
        name="search_knowledge_base",
        description="fake read-only search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search,
        semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
    )
    # Late core_tools imports register built-ins onto the patched registry;
    # pin the dispatch boundary itself so no built-in real handler can run.
    monkeypatch.setattr(registry, "execute_detailed", _execute_detailed)
    provider = _ExecutorFinalProvider()
    loop = PERAgentLoop(
        provider,
        _config(),
        organization_id=1,
        user_id=0,
        execution_mode=execution_mode,
    )
    loop.memory = _Memory()
    loop._planner = _Planner(("completed", "completed"))
    loop._executor = Executor(provider, loop.config, loop.memory, execution_mode=execution_mode)
    loop._write_experiences = AsyncMock()

    events = [event async for event in loop.run("真实 Executor 的原始 query")]
    chunks = [event.content for event in events if event.type == "chunk"]

    assert chunks == ["第20条证据：2小时与30分钟"]
    assert len(provider.final_calls) == 1
    assert provider.main_calls == 2
    assert provider.local_calls == 2
    assert sorted(handler_calls) == ["一", "二"]
    prompt = "\n".join(
        str(message["content"]) for message in provider.final_calls[0]["messages"]
    )
    assert "真实 Executor 的原始 query" in prompt
    assert "call-1#20" in prompt and "call-2#20" in prompt
    assert "一：第20条，2小时与30分钟" in prompt
    assert "二：第20条，2小时与30分钟" in prompt
    assert not any("局部未找到/无法生成" in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_replan_synthesizes_only_the_stable_plan_once():
    provider = _FakeProvider("只来自稳定计划")
    reflector = _Reflector(["replan", "pass"])
    loop = _loop(provider, statuses=("completed",), reflection=True, reflector=reflector)

    events = [event async for event in loop.run("q")]
    chunks = [event.content for event in events if event.type == "chunk"]
    prompt = _prompt(provider)

    assert loop._planner.calls == 2
    assert loop._executor.executed_plans == ["plan-1", "plan-2"]
    assert len(provider.calls) == 1
    assert chunks == ["只来自稳定计划"]
    assert "局部-plan-2-s1" in prompt
    assert "局部-plan-1-s1" not in prompt


@pytest.mark.asyncio
async def test_reflection_retry_is_complete_before_single_final_synthesis():
    provider = _FakeProvider("重试后的唯一答案")
    reflector = _Reflector(["retry"])
    loop = _loop(provider, statuses=("completed",), reflection=True, reflector=reflector)

    events = [event async for event in loop.run("q")]
    chunks = [event.content for event in events if event.type == "chunk"]
    prompt = _prompt(provider)

    assert loop._executor.retry_count == 1
    assert len(provider.calls) == 1
    assert chunks == ["重试后的唯一答案"]
    assert "局部-retry-s1" in prompt
    assert "局部-plan-1-s1" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("recommendation", ["reject", "revise", "error"])
async def test_quality_chain_reviews_same_packet_without_modifying_draft(recommendation):
    order: list[str] = []
    provider = _FakeProvider("不可被审查改写的最终草稿")
    original_create = provider.chat.completions.create

    async def _create(**kwargs):
        order.append("synthesizer")
        return await original_create(**kwargs)

    provider.chat.completions.create = _create
    reviewer = _Reviewer(recommendation, order)
    reflector = _Reflector(["pass"], order)
    loop = _loop(
        provider,
        statuses=("completed",),
        reflection=True,
        reflector=reflector,
        reviewer=reviewer,
    )

    events = [event async for event in loop.run("原始 query")]
    chunks = [event.content for event in events if event.type == "chunk"]

    assert order == ["reflector", "synthesizer", "reviewer"]
    assert chunks == ["不可被审查改写的最终草稿"]
    assert "HIGH-ISSUE-不得进入正文" not in chunks[0]
    assert loop.memory.interactions[-1][1] == chunks[0]
    assert len(reviewer.calls) == 1
    packet, draft, synthesis_messages = reviewer.calls[0]
    assert packet.query == "原始 query"
    assert packet.plan_id == "plan-1"
    assert draft == chunks[0]
    assert synthesis_messages == provider.calls[0]["messages"]


@pytest.mark.asyncio
async def test_all_failed_loop_skips_final_provider_and_preserves_failure_terminal_state():
    provider = _FakeProvider("不得出现")
    loop = _loop(provider, statuses=("failed", "skipped"))

    events = [event async for event in loop.run("q")]

    assert provider.calls == []
    assert not any(event.type == "chunk" for event in events)
    assert loop.run_failed is True
    assert "工具模式执行全败" in (loop.run_failure or "")
    assert loop.memory.interactions == [("q", "")]
    assert any(event.type == "done" for event in events)


@pytest.mark.asyncio
async def test_partial_success_keeps_failure_accounting_and_success_terminal_state():
    provider = _FakeProvider("基于成功证据，同时说明失败步骤")
    loop = _loop(provider, statuses=("completed", "failed"))

    events = [event async for event in loop.run("q")]
    chunks = [event.content for event in events if event.type == "chunk"]
    prompt = _prompt(provider)

    assert chunks == ["基于成功证据，同时说明失败步骤"]
    assert loop.run_failed is False
    assert "status=failed" in prompt and "执行失败" in prompt
    assert "证据-plan-1-s1" in prompt


@pytest.mark.asyncio
async def test_real_final_reviewer_uses_draft_and_same_prompt_without_rewriting_it():
    review_json = json.dumps(
        {
            "overall_assessment": "不可信",
            "issues_found": [
                {"severity": "high", "description": "问题", "suggestion": "建议"}
            ],
            "key_strengths": [],
            "recommendation": "reject",
        },
        ensure_ascii=False,
    )
    provider = _FakeProvider(review_json)
    reviewer = Reviewer(provider, _config())
    packet = FinalInputPacket.from_plan("UNIQUE-ORIGINAL-QUERY", _plan(("completed",)))
    synthesis_messages = [
        {"role": "system", "content": "synthesis system"},
        {"role": "user", "content": "同一证据包 call-A#20 第20条"},
    ]
    draft = "最终草稿保持原样"

    review = await reviewer.review_final(
        packet,
        draft,
        synthesis_messages=synthesis_messages,
    )

    assert review["recommendation"] == "reject"
    assert draft == "最终草稿保持原样"
    prompt = _prompt(provider)
    assert "同一证据包 call-A#20 第20条" in prompt
    assert "最终草稿保持原样" in prompt
    assert "UNIQUE-ORIGINAL-QUERY" in prompt
    assert "answer" not in review and "draft" not in review


@pytest.mark.asyncio
async def test_real_final_reviewer_unparsed_is_not_pass_and_does_not_change_draft():
    provider = _FakeProvider("not-json")
    reviewer = Reviewer(provider, _config())
    packet = FinalInputPacket.from_plan("q", _plan(("completed",)))
    draft = "不可改写"

    review = await reviewer.review_final(packet, draft)

    assert review["recommendation"] == "error"
    assert review["error_kind"] == "no_verdict"
    assert draft == "不可改写"


@pytest.mark.asyncio
async def test_tool_less_direct_answer_reuses_final_stage():
    provider = _FakeProvider("无工具也经过 final 阶段")
    loop = PERAgentLoop(
        provider,
        _config(enable_tools=False, enable_planning=False),
        organization_id=1,
        user_id=0,
    )
    loop.memory = _Memory()
    loop._write_experiences = AsyncMock()

    events = [event async for event in loop.run("直接原始问题", history=[{"role": "user", "content": "历史"}])]
    chunks = [event.content for event in events if event.type == "chunk"]

    assert chunks == ["无工具也经过 final 阶段"]
    assert len(provider.calls) == 1
    assert "直接原始问题" in _prompt(provider)
    assert loop.memory.interactions == [("直接原始问题", chunks[0])]


@pytest.mark.asyncio
async def test_final_provider_error_or_empty_response_emits_no_chunk():
    for provider in (_FakeProvider(error=RuntimeError("boom")), _FakeProvider(content="")):
        loop = _loop(provider, statuses=("completed",))
        events = [event async for event in loop.run("q")]

        assert not any(event.type == "chunk" for event in events)
        assert loop.run_failed is True
        assert loop.memory.interactions == [("q", "")]


def test_unified_final_flag_default_remains_false():
    assert ai_config.AISettings.model_fields["AGENT_UNIFIED_FINAL_ENABLED"].default is False


def test_legacy_packet_is_explicitly_marked_without_new_resume_semantics():
    legacy = PlanStep(
        id="old",
        description="旧 checkpoint 步骤",
        status="completed",
        result="旧局部结果",
        evidence_schema="legacy",
    )
    packet = FinalInputPacket.from_plan("q", Plan(id="old-plan", goal="g", steps=[legacy]))

    assert packet.legacy is True
    assert packet.degraded is True
    assert not hasattr(packet, "final_phase")
    assert not hasattr(packet, "resume")
