"""issue #242 第 5/10 条 + #239 minor-10 / #245 第 8 条的收账测试。

- **指标补埋**：`RAG_QUERY_REWRITE_TOTAL` / `RAG_CACHE_EVICTIONS` 此前全仓零调用点，
  各用"接线真的调了没有"的判别性测试钉住（变异：删埋点 → 红）；
- **契约对齐**：`EventType` 收录实际发射的 `retry_hint`；`ToolErrorType` 收录三个
  实际被赋值、此前却不在 Literal 的码；
- **直答步骤产生 StepRecord**：此前直答 run 的 run report / execution_context
  的 steps 恒为空。
"""
import typing
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.events import EventType
from app.agent.registry import ToolError
from app.rag.cache import BoundedLRUCache

# ── 指标补埋 ────────────────────────────────────────────────────────────────


def test_query_rewrite_counter_wired(monkeypatch):
    """变异点：删 query_processor 里的 RAG_QUERY_REWRITE_TOTAL.inc → 本用例红。"""
    import app.rag.query_processor as qp

    spy = MagicMock()
    monkeypatch.setattr(qp, "RAG_QUERY_REWRITE_TOTAL", spy)

    out = qp.rewrite_query_candidates("请 帮我 总结一下这份文档")

    assert len(out) > 1, "该查询应产生多个改写变体（用例前置条件）"
    spy.inc.assert_called_once_with(len(out) - 1)


def test_query_rewrite_counter_silent_for_single_variant(monkeypatch):
    """只产出原查询时不计数（语义 = 新增变体数）。"""
    import app.rag.query_processor as qp

    spy = MagicMock()
    monkeypatch.setattr(qp, "RAG_QUERY_REWRITE_TOTAL", spy)

    out = qp.rewrite_query_candidates("架构")

    assert len(out) == 1
    spy.inc.assert_not_called()


def test_cache_eviction_counter_wired(monkeypatch):
    """变异点：删 cache.set 淘汰处的 RAG_CACHE_EVICTIONS 埋点 → 本用例红。

    cache.py 在淘汰路径内以 `from app.core.prometheus import ...` 取指标，
    故补丁目标是 app.core.prometheus 上的名字。
    """
    import app.core.prometheus as prom

    spy = MagicMock()
    monkeypatch.setattr(prom, "RAG_CACHE_EVICTIONS", spy)

    c = BoundedLRUCache(max_size=100)  # 构造下限即 100
    for i in range(101):  # 第 101 条触发一次淘汰
        c.set(f"k{i}", i)

    spy.labels.assert_called_once_with(cache_type="memory")
    spy.labels.return_value.inc.assert_called_once()


# ── 契约对齐 ────────────────────────────────────────────────────────────────


def test_event_type_contract_includes_emitted_types():
    """变异点：从 events.py 的 EventType 删 "retry_hint" → 本用例红。"""
    args = typing.get_args(EventType)
    assert "retry_hint" in args, "executor 会发射 retry_hint，契约必须收录"
    assert "run_report" in args


def test_tool_error_type_contract_includes_assigned_codes():
    """变异点：删 registry.py Literal 里补的三个码 → 本用例红。"""
    args = typing.get_args(ToolError.ToolErrorType)
    for code in ("auth_expired_refreshable", "auth_required", "hook_error"):
        assert code in args, f"{code} 会被实际赋值，必须在 Literal 中"


# ── 直答步骤产生 StepRecord ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_answer_step_records_completion():
    """变异点：删 executor 直答早退处的 ctx.complete_step → 本用例红。"""
    from app.agent.config import AgentConfig
    from app.agent.exec_context import ExecutionContext
    from app.agent.executor import Executor
    from app.agent.planner import Plan, PlanStep

    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = "直接回答内容"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create

    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(client, AgentConfig(enable_planning=False, enable_reflection=False), memory)
    step = PlanStep(id="s1", description="直答步骤")
    plan = Plan(id="p1", goal="g", steps=[step])
    ctx = ExecutionContext(query="q")

    [e async for e in ex._execute_step_once(
        step=step, plan=plan, history=None, organization_id=1, user_id=0,
        enable_thinking=False, ctx=ctx,
    )]

    assert ctx.completed_steps, "直答步骤必须产生 StepRecord（#239 minor-10 / #245 第 8 条）"
    rec = ctx.completed_steps[-1]
    assert rec.step_id == "s1"
    assert rec.status == "completed"
