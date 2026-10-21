"""issue #202 接线验收：LLM 用量统一记账（含 #239 minor-2 / minor-9 收账）。

口径：`app.agent.usage.record_llm_usage` —— PER 各处调用点（planner 流式 /
executor 主调 / executor 合成 / reflector / loop 无工具分支 / reviewer /
工具内直连调用）统一入账：Prometheus `agent_tokens_total{model,direction}`
（#239 minor-9 起与 RAG 侧的 `rag_llm_tokens_total` 拆分独立）+
`ExecutionContext.total_tokens`。

此前 `total_tokens` 全仓零赋值——系统答不出"这次任务花了多少 token"。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发工具注册（executor 路径需要）
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.usage import record_llm_usage


def _usage(prompt: int, completion: int):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


def test_record_helper_accumulates_and_handles_missing_usage():
    ctx = ExecutionContext(query="t")
    resp = SimpleNamespace(usage=_usage(120, 30))
    assert record_llm_usage(resp, ctx) == 150
    assert ctx.total_tokens == 150
    # 无 usage（部分兼容服务器 / 流式未开 usage）不抛错、计 0
    assert record_llm_usage(SimpleNamespace(), ctx) == 0
    assert ctx.total_tokens == 150
    # ctx 缺省也可用（仅进指标）
    assert record_llm_usage(resp) == 150


def test_add_tokens_ignores_zero_and_negative():
    ctx = ExecutionContext(query="t")
    ctx.add_tokens(0)
    ctx.add_tokens(-5)
    assert ctx.total_tokens == 0
    ctx.add_tokens(7)
    assert ctx.total_tokens == 7


@pytest.mark.asyncio
async def test_executor_step_records_usage_into_ctx_and_step():
    """executor：主调 usage 记入 step.tokens_used；合成调用也计入 ctx.total_tokens。

    走完整工具流程（一次工具调用 + 一次合成），两次用量不同以便区分口径：
    step 级只记本步骤主调（250），ctx 级累计两次（250 + 150 = 400）。
    """
    from unittest.mock import AsyncMock, patch

    from app.agent.registry import tool_registry

    client = MagicMock()
    call_count = {"n": 0}

    async def _create(*, messages, tools=None, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        if call_count["n"] == 1:
            tc = MagicMock()
            tc.id = "tc1"
            tc.function.name = "search_knowledge_base"
            tc.function.arguments = "{}"
            msg.tool_calls = [tc]
            resp.usage = _usage(200, 50)  # 主调
        else:
            msg.tool_calls = None
            resp.usage = _usage(120, 30)  # 合成
        msg.content = "s"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    executor = Executor(client, AgentConfig(enable_planning=False, enable_reflection=False), memory)

    class _Ok:
        success = True
        error = None

        def __str__(self):
            return "ok"

    ctx = ExecutionContext(query="t")
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())):
        async for _ in executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
            ctx=ctx,
        ):
            pass

    assert ctx.total_tokens == 400, f"主调+合成应累计入账，实际 {ctx.total_tokens}"
    assert ctx.completed_steps[-1].tokens_used == 250, "步骤级只应记本步骤主调"


@pytest.mark.asyncio
async def test_reviewer_records_usage():
    from app.agent.reviewer import Reviewer

    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.content = '{"issues_found": [], "recommendation": "pass"}'
        msg.tool_calls = None
        choice.message = msg
        resp.choices = [choice]
        resp.usage = _usage(80, 20)
        return resp

    client.chat.completions.create = _create
    ctx = ExecutionContext(query="t")
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="d")])
    reviewer = Reviewer(client, AgentConfig())
    await reviewer.review(plan, {}, ctx=ctx)

    assert ctx.total_tokens == 100, f"reviewer usage 应入账，实际 {ctx.total_tokens}"


@pytest.mark.asyncio
async def test_reflector_records_usage():
    from app.agent.reflector import Reflector

    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.content = '{"decision": "pass", "reasoning": "ok", "gaps": []}'
        msg.tool_calls = None
        choice.message = msg
        resp.choices = [choice]
        resp.usage = _usage(60, 15)
        return resp

    client.chat.completions.create = _create
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    ctx = ExecutionContext(query="t")
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="d")])
    reflector = Reflector(client, AgentConfig(), memory)
    async for _ in reflector.reflect(plan, {"s1": {"result": "r"}}, ctx=ctx):
        pass

    assert ctx.total_tokens == 75, f"reflector usage 应入账，实际 {ctx.total_tokens}"


# ── 工具内直连 LLM 记账（issue #239 minor-2） ───────────────────────────────


def _fake_pipeline_with_llm(content: str, usage):
    """假 RAG pipeline：openai_client 返回带 usage 的响应。"""
    resp = SimpleNamespace(
        usage=usage,
        model="deepseek-chat",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )

    async def _create(*, model, messages, **kwargs):
        return resp

    client = MagicMock()
    client.chat.completions.create = _create
    return SimpleNamespace(openai_client=client)


@pytest.mark.asyncio
async def test_tool_internal_llm_call_counted_into_ctx():
    """translate_text 工具内直连 LLM：注册表注入 run_context → 用量进任务级累计。

    变异点：删 translation.py 的 record_llm_usage 调用（或删 run_context 形参
    使注册表不再注入 ctx）→ ctx 增量归零，本用例红。
    """
    from unittest.mock import patch

    from app.agent.registry import tool_registry
    from app.agent.run_context import RunContext

    pipeline = _fake_pipeline_with_llm("你好，世界", _usage(300, 100))
    ctx = ExecutionContext(query="t")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        out = await tool_registry.execute(
            "translate_text",
            {"text": "hello world", "target_language": "zh"},
            organization_id=1,
            user_id=0,
            run_context=run_context,
        )

    assert out == "你好，世界"
    assert ctx.total_tokens == 400, (
        f"工具内 LLM 调用须计入任务级 total_tokens（#239 minor-2），实际 {ctx.total_tokens}"
    )


@pytest.mark.asyncio
async def test_tool_internal_llm_call_counted_without_ctx_is_silent():
    """独立调用（无 run_context / 未挂 ctx）：不抛错，也不存在可累计的 ctx。"""
    from unittest.mock import patch

    from app.agent.registry import tool_registry

    pipeline = _fake_pipeline_with_llm("你好", _usage(10, 5))

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        out = await tool_registry.execute(
            "translate_text",
            {"text": "hi there", "target_language": "zh"},
        )

    assert out == "你好"


@pytest.mark.asyncio
async def test_core_tools_llm_call_counted_into_ctx():
    """core_tools 侧代表站点（generate_report）：同一注入路径须照常入账。"""
    from unittest.mock import patch

    from app.agent.registry import tool_registry
    from app.agent.run_context import RunContext

    pipeline = _fake_pipeline_with_llm("# 报告正文", _usage(500, 200))
    ctx = ExecutionContext(query="t")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        out = await tool_registry.execute(
            "generate_report",
            {
                "title": "T",
                "sections": '[{"heading": "h", "content": "c"}]',
            },
            organization_id=1,
            user_id=0,
            run_context=run_context,
        )

    assert out == "# 报告正文"
    assert ctx.total_tokens == 700, f"generate_report 工具内用量应入账，实际 {ctx.total_tokens}"


# ── 接线挂载点回归（issue #269 P2②/P2③：此前两处删除全量静默） ──────────────


@pytest.mark.asyncio
async def test_executor_mounts_exec_ctx_for_tool_internal_accounting():
    """P2②：executor → 工具链路的挂载点（`extra={"exec_ctx": ctx}`）必须把工具内
    用量送进本轮 ctx.total_tokens。

    与上文两条用例（手工构造 RunContext 直调注册表）不同，本用例**经 executor 的
    工具执行路径**驱动真工具（translate_text）：删掉 executor.py 的 exec_ctx 挂载
    → 工具内 400 tokens 丢账 → 800 处精确红。此前该变异在全量测试下静默
    （review_mx250 M1：866 passed），缺口由此闭合。
    """
    from unittest.mock import patch

    client = MagicMock()
    call_count = {"n": 0}

    async def _create(*, messages, tools=None, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        if call_count["n"] == 1:
            tc = MagicMock()
            tc.id = "tc1"
            tc.function.name = "translate_text"
            tc.function.arguments = '{"text": "hello world", "target_language": "zh"}'
            msg.tool_calls = [tc]
            resp.usage = _usage(200, 50)  # 主调：250
        else:
            msg.tool_calls = None
            resp.usage = _usage(120, 30)  # 合成：150
        msg.content = "s"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create

    # 工具内 LLM（translate_text 经 app.dependencies.get_rag_pipeline 取客户端）
    tool_pipeline = _fake_pipeline_with_llm("你好，世界", _usage(300, 100))

    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    executor = Executor(client, AgentConfig(enable_planning=False, enable_reflection=False), memory)

    ctx = ExecutionContext(query="t")
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])

    with patch("app.dependencies.get_rag_pipeline", return_value=tool_pipeline):
        async for _ in executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
            ctx=ctx,
        ):
            pass

    assert ctx.total_tokens == 800, (
        f"主调 250 + 合成 150 + 工具内 400 应全部入账，实际 {ctx.total_tokens}"
        "（删 executor 的 exec_ctx 挂载 → 工具内丢账 → 本断言红）"
    )


@pytest.mark.asyncio
async def test_composite_skill_inner_calls_counted_into_ctx():
    """P2③：复合技能（knowledge_analysis）内层工具的 LLM 用量经 run_context 透传
    计入任务级累计。

    链路（全真代码，只打桩 pipeline/ES 两个外部边界）：
      knowledge_analysis → search_knowledge_base（假检索返回带 "ID: doc1" 的结果）
      → extract_insights（假 ES + 假 LLM：300/100）→ generate_report（假 LLM：500/200）。

    变异验证：删 skills.py 任一内层 `run_context=run_context` 透传（如
    knowledge_analysis → generate_report）→ 对应用量丢账 → 1100 处红。
    此前该变异在全量测试下静默（review_mx250 M4：866 passed）。
    """
    from unittest.mock import AsyncMock, patch

    from app.agent.registry import tool_registry
    from app.agent.run_context import RunContext

    responses = [
        SimpleNamespace(  # extract_insights 的 LLM 响应
            usage=_usage(300, 100),
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"claims": []}'))],
        ),
        SimpleNamespace(  # generate_report 的 LLM 响应
            usage=_usage(500, 200),
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content="# 报告正文"))],
        ),
    ]
    calls = {"n": 0}

    async def _create(*, model, messages, **kwargs):
        resp = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return resp

    llm_client = MagicMock()
    llm_client.chat.completions.create = _create

    async def _search_kb(*, query, organization_id, top_k, document_ids=None):
        # snippet 带 "ID: doc1" → 技能会把它解析成内层 extract_insights 的入参
        return (
            [{"score": 0.9, "filename": "f.md", "snippet": "ID: doc1 | 文件名: f.md"}],
            None,
        )

    pipeline = SimpleNamespace(openai_client=llm_client, search_knowledge_base=_search_kb)
    es_hits = {"hits": {"hits": [{"_source": {"content": "全文内容", "filename": "f.md"}}]}}

    ctx = ExecutionContext(query="t")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})

    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch(
            "app.core.elasticsearch.ElasticsearchTools.search_documents",
            new=AsyncMock(return_value=es_hits),
        ),
    ):
        out = await tool_registry.execute(
            "knowledge_analysis",
            {"query": "主题"},
            organization_id=1,
            user_id=0,
            run_context=run_context,
        )

    assert out and "Error" not in out, f"技能应正常执行，实际: {out[:200]}"
    assert ctx.total_tokens == 1100, (
        f"内层 extract_insights 400 + generate_report 700 应经透传入账，"
        f"实际 {ctx.total_tokens}（删任一内层 run_context 透传 → 本断言红）"
    )


# ── 指标拆分（issue #239 minor-9） ──────────────────────────────────────────


def _real_metric_value(name: str, labels: dict) -> float:
    from app.core.prometheus import _get_real

    real = _get_real()
    assert real is not None, "conftest 应已开启 PROMETHEUS_ENABLED（真实指标）"
    return real[name].labels(**labels)._value.get()


def test_record_helper_emits_agent_metric_not_rag_metric():
    """agent 用量进专属 `agent_tokens_total{model,direction}`，不再写 RAG 指标。

    变异点：usage.py 回退为 `LLM_TOKENS`（旧共享口径）→ 本用例两处断言红。
    """
    in_labels = {"model": "deepseek-chat", "direction": "input"}
    out_labels = {"model": "deepseek-chat", "direction": "output"}
    before_in = _real_metric_value("AGENT_TOKENS", in_labels)
    before_out = _real_metric_value("AGENT_TOKENS", out_labels)
    before_rag_in = _real_metric_value("LLM_TOKENS", {"direction": "input"})

    ctx = ExecutionContext(query="t")
    resp = SimpleNamespace(usage=_usage(120, 30), model="deepseek-chat")
    assert record_llm_usage(resp, ctx) == 150

    assert _real_metric_value("AGENT_TOKENS", in_labels) == before_in + 120
    assert _real_metric_value("AGENT_TOKENS", out_labels) == before_out + 30
    assert _real_metric_value("LLM_TOKENS", {"direction": "input"}) == before_rag_in, (
        "agent 链路用量不得再写 RAG 侧的 rag_llm_tokens_total"
    )


def test_record_helper_model_label_falls_back_to_unknown():
    """流式包形态（SimpleNamespace(usage=...) 无 model）→ 标签回退 "unknown"。"""
    labels = {"model": "unknown", "direction": "input"}
    before = _real_metric_value("AGENT_TOKENS", labels)

    assert record_llm_usage(SimpleNamespace(usage=_usage(10, 5))) == 15

    assert _real_metric_value("AGENT_TOKENS", labels) == before + 10
