"""issue #242 第 5/10 条 + #239 minor-10 / #245 第 8 条的收账测试。

- **指标补埋**：`RAG_QUERY_REWRITE_TOTAL` / `RAG_CACHE_EVICTIONS` 此前全仓零调用点，
  各用"接线真的调了没有"的判别性测试钉住（变异：删埋点 → 红）；
- **契约对齐**：`EventType` 收录实际发射的 `retry_hint`；`ToolErrorType` 收录三个
  实际被赋值、此前却不在 Literal 的码；
- **直答步骤产生 StepRecord**：此前直答 run 的 run report / execution_context
  的 steps 恒为空。
"""
import typing
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.agent.events import EventType
from app.agent.registry import ToolError
from app.rag.cache import BoundedLRUCache

# ── 指标补埋 ────────────────────────────────────────────────────────────────


def test_query_rewrite_counter_wired(monkeypatch):
    """变异点：删 query_processor 里的 RAG_QUERY_REWRITE_TOTAL.inc → 本用例红。

    #250 第 3 条：查询换成产出 ≥3 条候选的样本 —— 原查询恰好产出 2 条，
    `inc(len(out)-1)` 恒等于 inc(1)，与"数事件（恒 inc(1)）"不可区分。
    """
    import app.rag.query_processor as qp

    spy = MagicMock()
    monkeypatch.setattr(qp, "RAG_QUERY_REWRITE_TOTAL", spy)

    out = qp.rewrite_query_candidates("请 帮我 总结一下这份文档，好吗？")

    assert len(out) == 3, f"该查询应产出 3 条候选（用例前置条件），实际 {len(out)}"
    assert spy.inc.call_args_list == [call(2)], (
        f"应恰好计入 2 条新增变体，实际 {spy.inc.call_args_list}"
    )


@pytest.mark.parametrize(
    ("query", "expected_new_variants"),
    [
        ("架构", 0),                                  # 无改写候选 → 不计数
        ("请 帮我 总结一下这份文档", 1),               # 2 条候选 → 计 1
        ("请 帮我 总结一下这份文档，好吗？", 2),        # 3 条候选 → 计 2
    ],
)
def test_query_rewrite_counter_counts_exact_new_variants(monkeypatch, query, expected_new_variants):
    """数值矩阵（#250 第 3 条）。

    变异点：计数改为"恒 inc(1)"→ 0/2 两行红；删 inc → 1/2 两行红；
    计数改为"数候选总数"→ 0 行红、1/2 两行红。
    """
    import app.rag.query_processor as qp

    spy = MagicMock()
    monkeypatch.setattr(qp, "RAG_QUERY_REWRITE_TOTAL", spy)

    out = qp.rewrite_query_candidates(query)

    assert len(out) - 1 == expected_new_variants, (
        f"用例前置：该查询应产出 {expected_new_variants + 1} 条候选，实际 {len(out)}"
    )
    if expected_new_variants:
        assert spy.inc.call_args_list == [call(expected_new_variants)]
    else:
        spy.inc.assert_not_called()


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


def test_tool_error_type_docstring_count_matches_literal():
    """#250 第 4 条：docstring 里的类别数必须与 Literal 实际条目数一致。

    变异点：把 ToolError docstring 改回 "one of 23 ToolErrorType literals"
    （陈旧计数）→ 本用例红。
    """
    args = typing.get_args(ToolError.ToolErrorType)
    assert f"one of {len(args)} ToolErrorType literals" in (ToolError.__doc__ or ""), (
        f"docstring 的类别数陈旧：Literal 实际 {len(args)} 条"
    )


# ── 直答步骤产生 StepRecord ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_answer_step_records_completion():
    """变异点：删 executor 直答早退处的 ctx.complete_step → 本用例红。

    #250 第 5 条：duration/tokens 此前也未传递（恒 0）——该路径 step_tokens
    有值（主调 usage 已采集）却未落账，run report 按步展示 token 时全是 0。
    """
    from types import SimpleNamespace

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
        resp.usage = SimpleNamespace(prompt_tokens=30, completion_tokens=10)
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
    assert rec.tokens_used == 40, f"直答步骤需落账本步主调用量（40），实际 {rec.tokens_used}"
    assert rec.duration_ms > 0, f"直答步骤需落账实际墙钟（>0），实际 {rec.duration_ms}"
