"""issue #202 接线验收：LLM 用量统一记账。

口径：`app.agent.usage.record_llm_usage` —— 六处调用点（planner 流式 / executor
主调 / executor 合成 / reflector / loop 无工具分支 / reviewer）统一入账：
Prometheus `rag_llm_tokens_total{direction}`（沿 RAG 侧既有口径）+
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
    executor = Executor(
        client, AgentConfig(enable_planning=False, enable_reflection=False), memory
    )

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
