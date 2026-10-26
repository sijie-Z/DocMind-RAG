"""最终综合阶段 1：局部事件与原始证据的判别测试。

只使用假 LLM、内存工具注册表与假账本；不访问真实模型或外部服务。
阶段 2/3 的最终 LLM、final-only resume 和 done.final_answer 不在本文件范围。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent import executor as executor_module
from app.agent import hooks as hook_events
from app.agent import registry as registry_module
from app.agent import tool_ledger
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import STEP_FAILURE_PLACEHOLDER, Executor
from app.agent.hooks import HookDecision
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolRegistry, ToolResult
from app.core.config import ai as ai_config


class _FakeMemory:
    def __init__(self) -> None:
        self.results: dict[str, dict] = {}
        self.interactions: list[tuple[str, str]] = []

    def reset_working(self) -> None:
        self.results = {}

    def get_all_results(self) -> dict:
        return dict(self.results)

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    def store_step_result(self, step_id, result) -> None:
        self.results[step_id] = result

    async def record_experience(self, **_kwargs) -> None:
        return None

    async def record_interaction(self, query, answer) -> None:
        self.interactions.append((query, answer))


class _ScriptedLLM:
    """工具调用走 script；无 tools 的调用是既有局部综合，不是最终综合。"""

    def __init__(self, script, *, synthesis_error: Exception | None = None) -> None:
        self.script = list(script)
        self.main_calls = 0
        self.synthesis_calls = 0
        self.synthesis_error = synthesis_error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, tools=None, **_kwargs):
        if tools is None:
            self.synthesis_calls += 1
            if self.synthesis_error is not None:
                raise self.synthesis_error
            return _response(content=f"local-synthesis-{self.main_calls}")
        outcome = self.script[min(self.main_calls, len(self.script) - 1)]
        self.main_calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for name, args, call_id in outcome
        ]
        return _response(tool_calls=calls)


def _response(*, content="", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=None,
        model="fake-local",
    )


def _config(**overrides) -> AgentConfig:
    values = {
        "enable_tools": True,
        "enable_planning": False,
        "enable_reflection": False,
        "enable_memory": False,
        "enable_skills": False,
        "enable_experience": False,
        "enable_thinking": False,
        "disabled_tools": [],
        "max_retries_per_step": 1,
        "approval_mode": "never",
    }
    values.update(overrides)
    return AgentConfig(**values)


def _one_step_plan() -> tuple[PlanStep, Plan]:
    step = PlanStep(id="s1", description="检索并总结", tool_hint="search_knowledge_base")
    return step, Plan(id="p1", goal="证据测试", steps=[step])


def _payloads(events, event_type="execution_step_result") -> list[dict[str, Any]]:
    result = []
    for event in events:
        if event.type != event_type:
            continue
        content = event.content
        result.append(json.loads(content) if isinstance(content, str) else content)
    return result


def _enable_unified(monkeypatch) -> None:
    monkeypatch.setattr(ai_config, "unified_final_enabled", lambda: True, raising=False)


@pytest.fixture(autouse=True)
def _default_flag_off(monkeypatch):
    monkeypatch.setattr(ai_config, "unified_final_enabled", lambda: False, raising=False)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "allow_request", lambda: True)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "record_success", lambda: None)
    monkeypatch.setattr(executor_module._LLM_BREAKER, "record_failure", lambda: None)


@pytest.fixture
def local_registry(monkeypatch) -> ToolRegistry:
    registry = ToolRegistry()
    monkeypatch.setattr(executor_module, "tool_registry", registry)
    monkeypatch.setattr(registry_module, "tool_registry", registry)
    monkeypatch.setattr(executor_module, "compute_backoff", lambda *_args: 0.0)
    return registry


def _chunk_spy(calls: list[str]):
    async def _spy(*, step, plan, history, organization_id, user_id, enable_thinking, ctx=None):
        calls.append(step.id)
        yield AgentEvent(type="chunk", content=f"local-{step.id}", plan_step_id=step.id)

    return _spy


@pytest.mark.asyncio
async def test_default_false_preserves_chunk_and_legacy_checkpoint_shape():
    step, plan = _one_step_plan()
    executor = Executor(_ScriptedLLM([]), _config(), _FakeMemory())
    executor._execute_step_with_retry = _chunk_spy([])

    events = [event async for event in executor.execute(plan, organization_id=1)]

    assert [(event.type, event.content) for event in events if event.type == "chunk"] == [
        ("chunk", "local-s1")
    ]
    assert _payloads(events) == []
    assert set(step.to_checkpoint_dict()) == {
        "id",
        "description",
        "dependencies",
        "tool_hint",
        "status",
        "result",
        "error_context",
        "retry_count",
        "parallel_group",
        "risk_level",
        "retry_strategy",
        "fallback_tool",
        "fallback_chain",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_mode", ["serial", "dag"])
async def test_unified_serial_and_parallel_steps_never_emit_local_chunks(
    monkeypatch, execution_mode
):
    _enable_unified(monkeypatch)
    steps = [PlanStep(id="s1", description="一"), PlanStep(id="s2", description="二")]
    plan = Plan(id="p1", goal="g", steps=steps)
    executor = Executor(_ScriptedLLM([]), _config(), _FakeMemory(), execution_mode=execution_mode)
    executed: list[str] = []
    executor._execute_step_with_retry = _chunk_spy(executed)

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payloads = _payloads(events)

    assert not any(event.type == "chunk" for event in events)
    assert sorted(executed) == ["s1", "s2"]
    assert sorted(payload["step_id"] for payload in payloads) == ["s1", "s2"]
    for payload in payloads:
        assert payload["status"] == "completed"
        assert payload["result"] == f"local-{payload['step_id']}"
        assert payload["local_synthesis"]["content"] == payload["result"]
        assert payload["local_synthesis"]["truncated"] is False
        assert payload["evidence"] == []


@pytest.mark.asyncio
async def test_unified_direct_llm_step_becomes_step_result_not_final_chunk(monkeypatch):
    _enable_unified(monkeypatch)
    step, plan = _one_step_plan()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    # 无工具直答仍由既有 Executor 完成；阶段 2 才会把它迁入最终综合。
    client.chat.completions.create = AsyncMock(
        return_value=_response(content="direct local answer")
    )
    executor = Executor(client, _config(enable_tools=False), _FakeMemory())

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payloads = _payloads(events)

    assert not any(event.type == "chunk" for event in events)
    assert len(payloads) == 1
    assert payloads[0]["result"] == "direct local answer"
    assert payloads[0]["evidence"] == []


def _search_envelope(call_marker: str, *, size: int = 2600) -> str:
    body = f"{call_marker}-" + ("原始证据" * 1000)
    body = body[:size]
    return json.dumps(
        {
            "kind": "docmind.search_results/v1",
            "sources": [
                {
                    "index": 1,
                    "score": 0.9,
                    "relevanceScore": None,
                    "filename": "same-name.pdf",
                    "documentId": f"doc-{call_marker}",
                }
            ],
            "content": f"[1] (0.90) same-name.pdf\n{body}",
            "evidence": [{"index": 1, "content": body}],
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_final_attempt_evidence_is_full_post_hook_and_ledger_sourced(
    monkeypatch, local_registry
):
    _enable_unified(monkeypatch)
    raw_by_query = {"a": _search_envelope("A"), "b": _search_envelope("B")}
    handler_calls: list[str] = []

    async def search(query: str):
        handler_calls.append(query)
        return raw_by_query[query]

    local_registry.register(
        name="search_knowledge_base",
        description="fake search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search,
    )

    async def _execute_detailed(name, args, **_kwargs):
        assert name == "search_knowledge_base"
        handler_calls.append(args["query"])
        return ToolResult.ok(raw_by_query[args["query"]])

    monkeypatch.setattr(local_registry, "execute_detailed", _execute_detailed)
    llm = _ScriptedLLM(
        [
            [
                ("search_knowledge_base", {"query": "a"}, "call-A"),
                ("search_knowledge_base", {"query": "b"}, "call-B"),
            ]
        ],
        synthesis_error=RuntimeError("local synthesis unavailable"),
    )
    executor = Executor(llm, _config(), _FakeMemory())
    executor.tool_ledger_task_id = "task-evidence"
    ledger_results: dict[str, str] = {}

    async def _record(task_id, call_id, *, success, result="", error_type=None):
        assert task_id == "task-evidence"
        ledger_results[call_id] = str(result)

    monkeypatch.setattr(tool_ledger, "register_calls", AsyncMock())
    monkeypatch.setattr(tool_ledger, "mark_dispatched", AsyncMock())
    monkeypatch.setattr(tool_ledger, "record_result", _record)

    async def _post_hook(event, payload, *, user_id, organization_id):
        if event != hook_events.TOOLS_POST_EXECUTE:
            return HookDecision(), []
        result = dict(payload["result"])
        envelope = json.loads(result["content"])
        envelope["post_hook"] = True
        result["content"] = json.dumps(envelope, ensure_ascii=False)
        return HookDecision(action="modify", payload={"result": result}), []

    monkeypatch.setattr(executor, "_emit_hooks", _post_hook)
    step, plan = _one_step_plan()

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payloads = _payloads(events)

    assert handler_calls == ["a", "b"]
    assert not any(event.type == "chunk" for event in events)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["local_synthesis"]["content"].startswith("找到以下信息")
    assert len(payload["evidence"]) == 2
    source_keys = []
    for record, call_id in zip(payload["evidence"], ["call-A", "call-B"], strict=True):
        raw = record["content"]
        assert json.loads(raw)["post_hook"] is True
        assert raw == ledger_results[call_id]
        assert len(raw) > 2000, "不得用 500 字账本或 2000 字局部摘要冒充原始证据"
        assert record["original_length"] == len(raw)
        assert record["stored_length"] == len(raw)
        assert record["truncated"] is False
        assert record["status"] == "completed"
        assert record["error"] is None
        assert record["sources"][0]["documentId"] == f"doc-{call_id[-1]}"
        assert len(record["evidence"]) == 1
        source_keys.append(record["evidence"][0]["key"])
    assert source_keys == ["call-A#1", "call-B#1"]
    assert len(set(source_keys)) == 2, "不同 tool call 的原始 index=1 不得互相覆盖"
    assert step.evidence == payload["evidence"]


@pytest.mark.asyncio
async def test_only_final_attempt_outcome_is_promoted(monkeypatch, local_registry):
    _enable_unified(monkeypatch)
    calls = 0

    async def retryable():
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolResult.fail(code="timeout", message="OLD-ATTEMPT-SECRET")
        return ToolResult.ok("NEW-ATTEMPT-EVIDENCE")

    local_registry.register(
        name="retry_probe",
        description="retry probe",
        parameters={"type": "object", "properties": {}},
        handler=retryable,
        semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
    )
    llm = _ScriptedLLM(
        [
            [("retry_probe", {}, "old-call")],
            [("retry_probe", {}, "new-call")],
        ]
    )
    executor = Executor(llm, _config(), _FakeMemory())
    step = PlanStep(id="s1", description="retry", tool_hint="retry_probe")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payload = _payloads(events)[0]
    serialized = json.dumps(payload, ensure_ascii=False)

    assert calls == 2
    assert payload["attempt"] == 1
    assert [item["tool_call_id"] for item in payload["evidence"]] == ["new-call"]
    assert "NEW-ATTEMPT-EVIDENCE" in serialized
    assert "OLD-ATTEMPT-SECRET" not in serialized
    assert payload["local_synthesis"]["content"] == "local-synthesis-2"
    assert "local-synthesis-1" not in serialized


@pytest.mark.asyncio
async def test_retry_safe_false_is_never_replayed_for_evidence(monkeypatch, local_registry):
    _enable_unified(monkeypatch)
    calls = 0

    async def unsafe():
        nonlocal calls
        calls += 1
        return ToolResult.fail(code="timeout", message="unsafe failure")

    local_registry.register(
        name="unsafe_probe",
        description="unsafe probe",
        parameters={"type": "object", "properties": {}},
        handler=unsafe,
        semantic={"type": "mutating", "idempotent": False, "retry_safe": False},
    )
    llm = _ScriptedLLM([[("unsafe_probe", {}, "unsafe-call")]])
    executor = Executor(llm, _config(), _FakeMemory())
    step = PlanStep(id="s1", description="unsafe", tool_hint="unsafe_probe")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payload = _payloads(events)[0]

    assert calls == 1
    assert llm.main_calls == 1
    assert payload["status"] == "failed"
    assert payload["evidence"][0]["status"] == "failed"
    assert payload["evidence"][0]["error"]["code"] == "timeout"
    assert STEP_FAILURE_PLACEHOLDER in payload["local_synthesis"]["content"]
    assert not any(event.type == "chunk" for event in events)


@pytest.mark.asyncio
async def test_retry_budget_gate_preserves_last_executed_attempt_evidence(
    monkeypatch, local_registry
):
    """attempt1 预算早退不得把 attempt0 的 post-hook 证据换成空 fallback。"""
    _enable_unified(monkeypatch)
    ctx = ExecutionContext(query="budget retry")
    calls = 0
    post_hook_raw = "POST-HOOK-BUDGET-" + ("原始失败证据" * 100)

    async def retryable():
        nonlocal calls
        calls += 1
        ctx.total_tokens = 100
        return ToolResult.fail(code="timeout", message="pre-hook-timeout")

    local_registry.register(
        name="budget_retry_probe",
        description="budget retry probe",
        parameters={"type": "object", "properties": {}},
        handler=retryable,
        semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
    )
    llm = _ScriptedLLM([[("budget_retry_probe", {}, "budget-call-0")]])
    executor = Executor(
        llm,
        _config(max_total_tokens=100, max_retries_per_step=1),
        _FakeMemory(),
    )

    async def _post_hook(event, payload, *, user_id, organization_id):
        if event != hook_events.TOOLS_POST_EXECUTE:
            return HookDecision(), []
        result = dict(payload["result"])
        result["content"] = f"Error: [timeout] {post_hook_raw}"
        return HookDecision(action="modify", payload={"result": result}), []

    monkeypatch.setattr(executor, "_emit_hooks", _post_hook)
    step = PlanStep(id="s1", description="budget retry", tool_hint="budget_retry_probe")
    plan = Plan(id="p1", goal="g", steps=[step])
    checkpoints = []

    async def _on_step_done(done_step):
        checkpoints.append(done_step.to_checkpoint_dict())

    events = [
        event
        async for event in executor.execute(
            plan,
            organization_id=1,
            ctx=ctx,
            on_step_done=_on_step_done,
        )
    ]
    payload = _payloads(events)[0]

    assert calls == 1, "预算门不得为收集证据重复派发工具"
    assert llm.main_calls == 1, "attempt1 必须在新的 LLM 调用前被预算门拦截"
    assert payload["attempt"] == 0
    assert payload["status"] == "failed"
    assert payload["error"].startswith("budget_exceeded")
    assert len(payload["evidence"]) == 1
    evidence = payload["evidence"][0]
    assert evidence["tool_call_id"] == "budget-call-0"
    assert evidence["status"] == "failed"
    assert evidence["error"]["code"] == "timeout"
    assert evidence["content"] == f"Error: [timeout] {post_hook_raw}"
    assert evidence["original_length"] == len(evidence["content"])
    assert evidence["stored_length"] == len(evidence["content"])
    assert evidence["truncated"] is False
    assert payload["local_synthesis"]["content"] == "local-synthesis-1"
    assert step.evidence == payload["evidence"]
    assert checkpoints[0]["evidence"] == payload["evidence"]
    assert checkpoints[0]["local_synthesis"] == payload["local_synthesis"]


@pytest.mark.asyncio
async def test_initial_budget_gate_has_no_fabricated_attempt_evidence(monkeypatch, local_registry):
    """首次派发前就超预算：零调用、零证据，不能虚构一个已执行 attempt。"""
    _enable_unified(monkeypatch)
    ctx = ExecutionContext(query="initial budget")
    ctx.total_tokens = 100
    calls = 0

    async def forbidden_tool():
        nonlocal calls
        calls += 1
        return ToolResult.ok("must not run")

    local_registry.register(
        name="initial_budget_probe",
        description="initial budget probe",
        parameters={"type": "object", "properties": {}},
        handler=forbidden_tool,
    )
    llm = _ScriptedLLM([[("initial_budget_probe", {}, "never-call")]])
    executor = Executor(llm, _config(max_total_tokens=100), _FakeMemory())
    step = PlanStep(id="s1", description="initial budget", tool_hint="initial_budget_probe")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [
        event
        async for event in executor._execute_single_step(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
            ctx=ctx,
        )
    ]
    payload = _payloads(events)[0]

    assert calls == 0
    assert llm.main_calls == 0
    assert llm.synthesis_calls == 0
    assert payload["attempt"] == 0
    assert payload["status"] == "failed"
    assert payload["error"].startswith("budget_exceeded")
    assert payload["evidence"] == []
    assert payload["local_synthesis"]["content"] == ""
    assert step.evidence == []


@pytest.mark.asyncio
async def test_normal_fallback_promotes_only_fallback_attempt(monkeypatch, local_registry):
    """正常 fallback 是新的最终 attempt，主工具证据和局部综合不得混入。"""
    _enable_unified(monkeypatch)

    async def primary():
        return ToolResult.fail(code="timeout", message="PRIMARY-OLD-EVIDENCE")

    async def fallback():
        return ToolResult.ok("FALLBACK-NEW-EVIDENCE")

    for name, handler in (("primary_probe", primary), ("fallback_probe", fallback)):
        local_registry.register(
            name=name,
            description=name,
            parameters={"type": "object", "properties": {}},
            handler=handler,
            semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
        )
    llm = _ScriptedLLM(
        [
            [("primary_probe", {}, "primary-call")],
            [("fallback_probe", {}, "fallback-call")],
        ]
    )
    executor = Executor(llm, _config(max_retries_per_step=0), _FakeMemory())
    step = PlanStep(
        id="s1",
        description="fallback",
        tool_hint="primary_probe",
        fallback_tool="fallback_probe",
    )
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [event async for event in executor.execute(plan, organization_id=1)]
    payload = _payloads(events)[0]
    serialized = json.dumps(payload, ensure_ascii=False)

    assert llm.main_calls == 2
    assert payload["attempt"] == 1
    assert payload["status"] == "completed"
    assert [item["tool_call_id"] for item in payload["evidence"]] == ["fallback-call"]
    assert payload["local_synthesis"]["content"] == "local-synthesis-2"
    assert "FALLBACK-NEW-EVIDENCE" in serialized
    assert "PRIMARY-OLD-EVIDENCE" not in serialized
    assert "local-synthesis-1" not in serialized


def test_checkpoint_evidence_roundtrip_and_explicit_legacy():
    step = PlanStep(id="s1", description="一", status="completed", result="局部摘要")
    raw = "完整原始证据" * 400
    evidence = [
        {
            "schema": "docmind.tool_evidence/v1",
            "step_id": "s1",
            "attempt": 0,
            "tool_call_id": "call-1",
            "tool": "search_knowledge_base",
            "status": "completed",
            "error": None,
            "content": raw,
            "original_length": len(raw),
            "stored_length": len(raw),
            "truncated": False,
            "sources": [],
            "evidence": [],
        }
    ]
    local = {
        "content": "完整局部综合" * 500,
        "original_length": len("完整局部综合" * 500),
        "stored_length": len("完整局部综合" * 500),
        "truncated": False,
    }
    step.evidence = evidence
    step.local_synthesis = local
    step.evidence_schema = "docmind.step_evidence/v1"

    restored = PlanStep.from_checkpoint_dict(step.to_checkpoint_dict())

    assert restored.evidence_schema == "docmind.step_evidence/v1"
    assert restored.evidence == evidence
    assert restored.local_synthesis == local
    assert restored.status == "completed"
    legacy = PlanStep.from_checkpoint_dict(
        {
            "id": "old-1",
            "description": "旧步骤",
            "status": "completed",
            "result": "旧结果",
            "dependencies": [],
        }
    )
    assert legacy.evidence_schema == "legacy"
    assert legacy.evidence == []
    assert legacy.local_synthesis is None


@pytest.mark.asyncio
async def test_legacy_completed_checkpoint_step_is_not_reexecuted(monkeypatch):
    _enable_unified(monkeypatch)
    legacy = PlanStep.from_checkpoint_dict(
        {"id": "s1", "description": "旧步骤", "status": "completed", "result": "旧结果"}
    )
    plan = Plan(id="p1", goal="g", steps=[legacy], completed_steps=1)
    executor = Executor(_ScriptedLLM([]), _config(), _FakeMemory())

    async def _forbidden(**_kwargs):
        raise AssertionError("legacy completed 步骤不得因缺 evidence 自动重跑")
        yield  # pragma: no cover

    monkeypatch.setattr(executor, "_execute_single_step", _forbidden)
    events = [event async for event in executor.execute(plan, organization_id=1)]

    assert events == []
    assert legacy.status == "completed"
    assert legacy.evidence_schema == "legacy"


def test_search_envelope_evidence_is_optional_additive_and_keeps_legacy_fields():
    from app.agent.core_tools import _build_search_payload

    entries = [
        (0.9, 0.8, "same.pdf", "doc-1", "第一源完整片段"),
        (0.7, None, "same.pdf", "doc-2", "第二源完整片段"),
    ]

    legacy = json.loads(_build_search_payload(entries))
    enriched = json.loads(_build_search_payload(entries, include_evidence=True))

    assert list(legacy) == ["kind", "sources", "content"]
    assert enriched["kind"] == legacy["kind"]
    assert enriched["sources"] == legacy["sources"]
    assert enriched["content"] == legacy["content"]
    assert enriched["evidence"] == [
        {"index": 1, "content": "第一源完整片段"},
        {"index": 2, "content": "第二源完整片段"},
    ]


class _OneStepPlanner:
    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="p1")
        yield AgentEvent(type="plan_step", plan_id="p1", plan_step_id="s1", content="旧替身步骤")
        yield AgentEvent(type="plan_complete", plan_id="p1", plan_goal="g")


class _LegacyChunkExecutor:
    """模拟旧 Executor/测试替身：执行阶段只吐 chunk，没有结构化步骤事件。"""

    def __init__(self) -> None:
        self.pending_control = ""

    def reset_for_request(self) -> None:
        self.pending_control = ""

    async def execute(self, *, plan, **_kwargs):
        text = "local-A-local-B"
        plan.steps[0].status = "completed"
        plan.steps[0].result = text
        plan.completed_steps = 1
        yield AgentEvent(type="chunk", content="local-A-", plan_step_id="s1")
        yield AgentEvent(type="chunk", content="local-B", plan_step_id="s1")


def _loop_with_legacy_executor() -> PERAgentLoop:
    loop = PERAgentLoop(SimpleNamespace(), _config(), organization_id=1, user_id=0)
    loop.memory = _FakeMemory()
    loop._planner = _OneStepPlanner()
    loop._executor = _LegacyChunkExecutor()
    return loop


@pytest.mark.asyncio
async def test_loop_normalizes_legacy_executor_chunks_without_final_answer_leak(
    monkeypatch,
):
    _enable_unified(monkeypatch)
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock())
    loop = _loop_with_legacy_executor()

    events = [event async for event in loop.run("原始问题")]
    payloads = _payloads(events)

    assert not any(event.type == "chunk" for event in events)
    assert len(payloads) == 1
    assert payloads[0]["result"] == "local-A-local-B"
    assert payloads[0]["legacy_chunk_normalized"] is True
    assert loop.memory.interactions == [("原始问题", "")]
    assert any(event.type == "done" for event in events)


@pytest.mark.asyncio
async def test_default_loop_keeps_legacy_chunks_and_saved_answer(monkeypatch):
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock())
    loop = _loop_with_legacy_executor()

    events = [event async for event in loop.run("原始问题")]

    assert "".join(event.content for event in events if event.type == "chunk") == "local-A-local-B"
    assert loop.memory.interactions == [("原始问题", "local-A-local-B")]


def test_acp_execution_step_result_remains_diagnostic_only():
    from app.acp.mapping import event_to_update

    event = AgentEvent(
        type="execution_step_result",
        content=json.dumps({"step_id": "s1", "result": "诊断，不是正文"}),
    )
    assert event_to_update(event) is None
