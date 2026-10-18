"""issue #293 收账测试：Agent 可信度双补丁。

两条"报告说谎"链（recon_except_swallow 第 3/4 条）：

① registry 归一化（:537-541）把"工具 except 后返回的错误字符串"包成
   success=True → SSE 发 tool_result、指标记 success、findings 记 high、
   run_report 全绿。修复选择"迁移工具到 ToolResult.fail"（web_search /
   translation / mcp_bridge / data_analysis 首批）而不是内容启发式 ——
   本文件同时钉住**误伤边界**（内容合法地长得像错误串的成功输出不得被判失败）。

② executor 重试耗尽/优雅降级路径（:626-628）只回写 step/plan、不写 ctx.failures
   → run_report grade_hint=ok / clean_success=True 与计划里的 ❌ 自相矛盾。
   修复：终态补 complete_step(...,"failed")（ctx.failures 的唯一写者）+ 带工具
   失败的 attempt 不再伪造 completed 记录 + run_report clean_success 纳入
   ctx.failures。

变异矩阵（每条应精确红）：
    M1  web_search except 分支还原为返回错误字符串 → 用例 1 / 7 红；
    M2  translate_text 校验守卫还原为 "Error: ..." 字符串 → 用例 3 红；
    M3  删 executor 终态 ctx.complete_step 记账 → 用例 5 / 7 红；
    M4  run_report 恢复 `clean_success = not fatal and review != "reject"` → 用例 5 / 7 红；
    M5  _execute_step_once 尾部恢复无条件 completed 记录 → 用例 7 红（假 completed 复发）；
    M6  删 mcp_bridge 未知 server 的结构化失败 → 用例 2 红；
    M7  删 tool_not_allowed 分支的 attempt_error_detail 赋值（#293 评审 R1 复现）→
        用例 8 红（同一 attempt 出现 completed+failed 双记录）。
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册（含本文件涉及的全部工具）
import app.agent.tools.web_search as ws_mod
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry
from app.agent.run_report import build_run_report, render_run_report_text

# ── 假件 ────────────────────────────────────────────────────────────────────


def _failing_ddgs(exc: Exception) -> list[tuple[str, types.ModuleType]]:
    """假搜索后端：进入上下文管理器即抛异常（模拟上游故障）。

    web_search 现为「新名 ddgs 优先、旧名 duckduckgo_search 兜底」两段 import
    （harness 审计·web 依赖补齐）——同一假件同时注入两个模块名，
    无论代码走到哪段 import 都命中。
    """

    class DDGS:
        def __enter__(self):
            raise exc

        def __exit__(self, *a):
            return False

    out: list[tuple[str, types.ModuleType]] = []
    for name in ("ddgs", "duckduckgo_search"):
        mod = types.ModuleType(name)
        mod.DDGS = DDGS
        out.append((name, mod))
    return out


def _fake_llm_toolclient(tool_name: str, args_json: str, synthesis: str = "合成答案"):
    """主调（带 tools）恒发一个 tool_call；合成调用（无 tools）返回文本。"""
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        if tools:
            tc = MagicMock()
            tc.id = "tc1"
            tc.function.name = tool_name
            tc.function.arguments = args_json
            msg.tool_calls = [tc]
            msg.content = ""
        else:
            msg.tool_calls = None
            msg.content = synthesis
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        resp.model = "fake"
        resp.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return resp

    client.chat.completions.create = _create
    return client


def _memory() -> AgentMemoryBridge:
    return AgentMemoryBridge(agent_id="test-293", organization_id=1, user_id=0)


def _failing_web_search_env(monkeypatch, exc: Exception):
    for name, mod in _failing_ddgs(exc):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(ws_mod, "_check_rate_limit", AsyncMock(return_value=True))


# ── ① 迁移后的工具：错误返回一律 success=False（经注册表路径） ─────────────


@pytest.mark.asyncio
async def test_web_search_error_string_no_longer_wrapped_as_success(monkeypatch):
    """web_search except 分支此前返回 'Web search error: ...' 字符串 → 被包装成
    success=True。修复后必须是结构化失败（M1：还原字符串 → 本用例红）。"""
    _failing_web_search_env(monkeypatch, RuntimeError("boom"))

    result = await tool_registry.execute_detailed("web_search", {"query": "q"}, user_id=1)

    assert result.success is False, f"工具错误不得再被包成功: {result.to_dict()}"
    assert result.error is not None and result.error.code == "unknown"
    assert str(result).startswith("Error:"), "str() 契约保持 'Error: [code] ...'"


@pytest.mark.asyncio
async def test_mcp_unknown_server_is_structured_failure():
    """mcp_bridge 未知 server 此前返回 'Error: unknown MCP server ...' 字符串
    （M6：还原为字符串 → 本用例红）。"""
    result = await tool_registry.execute_detailed(
        "mcp_call", {"server": "nope", "tool": "t"},
    )
    assert result.success is False
    assert result.error is not None and result.error.code == "validation_error"


@pytest.mark.asyncio
async def test_translate_short_text_is_structured_failure():
    """translate_text 校验守卫此前返回 'Error: Text too short to translate.'
    （M2：还原为字符串 → 本用例红）。"""
    result = await tool_registry.execute_detailed(
        "translate_text", {"text": "x", "target_language": "en"},
    )
    assert result.success is False
    assert result.error is not None and result.error.code == "validation_error"


@pytest.mark.asyncio
async def test_analyze_data_invalid_input_is_structured_failure():
    """data_analysis 非法输入此前返回 'Error: Invalid JSON. ...' 字符串。"""
    result = await tool_registry.execute_detailed("analyze_data", {"data": "{not json"})
    assert result.success is False
    assert result.error is not None and result.error.code == "validation_error"


@pytest.mark.asyncio
async def test_compare_documents_missing_org_fails_closed_structured():
    """compare_documents 缺组织上下文此前返回 'Error: 无法确定组织范围...' 字符串。"""
    result = await tool_registry.execute_detailed(
        "compare_documents", {"document_ids": ["a", "b"]},
    )
    assert result.success is False
    assert result.error is not None and result.error.code == "auth_required"


# ── ② 误伤边界：内容合法地长得像错误串的成功输出不得被判失败 ────────────────


@pytest.mark.asyncio
async def test_translation_output_that_looks_like_error_stays_success(monkeypatch):
    """判别用例（防误伤）：把「错误：文件未找到」译成英文，成功输出就是
    'Error: File not found' —— 它必须以 success=True 全链路通过。

    这正是修复拒绝"注册表按字符串前缀启发式判失败"的原因（任何 startswith
    ('Error:') 启发式都会在此误判）；若将来有人加回内容启发式，本用例红。
    """
    pipeline = SimpleNamespace(openai_client=MagicMock())

    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Error: File not found"))],
            usage=None,
        )

    pipeline.openai_client.chat.completions.create = _create
    monkeypatch.setattr("app.dependencies.get_rag_pipeline", lambda: pipeline)

    result = await tool_registry.execute_detailed(
        "translate_text", {"text": "错误：文件未找到", "target_language": "en"},
    )

    assert result.success is True, f"合法译文被误判为失败: {result.to_dict()}"
    assert result.data == "Error: File not found"


@pytest.mark.asyncio
async def test_code_execution_stdout_that_looks_like_error_stays_success():
    """判别用例（防误伤）：用户代码 print('Error: ...') 的 stdout 是合法成功输出。

    同"内容启发式不可取"的第二类反例（execute_python 的 stdout 是任意文本）。
    """
    result = await tool_registry.execute_detailed(
        "execute_python",
        {"code": "print('Error: simulated user output')"},
        organization_id=1,
    )
    assert result.success is True, f"合法 stdout 被误判为失败: {result.to_dict()}"
    assert "Error: simulated user output" in str(result.data)


# ── ③ executor 重试耗尽 → 记账 + run_report 全维度如实 ─────────────────────


class _ScriptedExecutor(Executor):
    """s1 正常完成（照真实路径写 completed 记录）；s2 恒 timeout（可重试）。"""

    async def _execute_step_once(
        self, step, plan, history, organization_id, user_id, enable_thinking,
        error_context="", ctx=None,
    ):
        if step.id == "s1":
            if ctx is not None:
                ctx.complete_step(step.id, step.description, step.tool_hint, "completed", result="结果一")
            yield AgentEvent(type="chunk", content="结果一", plan_id=plan.id, plan_step_id=step.id)
        else:
            yield AgentEvent(
                type="tool_error", tool_name="fake_tool", content="tools timed out",
                error_type="timeout", plan_id=plan.id, plan_step_id=step.id,
            )


@pytest.mark.asyncio
async def test_retry_exhausted_step_lands_in_failures_and_report():
    """重试耗尽步必须进 ctx.failures，run_report 不得再报干净成功。

    M3：删终态 complete_step → ctx.failures 空 → 红；
    M4：run_report 不看 ctx.failures → clean_success True → 红。
    """
    cfg = AgentConfig(
        enable_planning=False, enable_reflection=False,
        max_retries_per_step=1,  # 2 次 attempt 后耗尽
    )
    executor = _ScriptedExecutor(MagicMock(), cfg, _memory(), execution_mode="serial")
    ctx = ExecutionContext(query="q-293")
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="第一步", tool_hint="search_knowledge_base"),
        PlanStep(id="s2", description="第二步（必失败）", tool_hint="web_search"),
    ])

    events = [e async for e in executor.execute(plan, organization_id=1, ctx=ctx)]

    assert plan.steps[1].status == "failed"
    assert ctx.failures, "重试耗尽必须点亮 ctx.failures（修复前恒空）"
    assert any("s2" in f and "timed out" in f for f in ctx.failures), ctx.failures
    assert any(
        r.step_id == "s2" and r.status == "failed" and r.error
        for r in ctx.completed_steps
    ), "终态失败必须落一条 failed StepRecord 供 run_report 展示"
    assert any(e.type == "tool_error" and "步骤失败" in e.content for e in events)

    report = build_run_report(ctx, final_output="答案")
    assert report["evaluation"]["clean_success"] is False, "存在失败步不得报干净成功"
    assert report["evaluation"]["grade_hint"] == "has-failures"
    text = render_run_report_text(report)
    assert "FAILURE:" in text and "clean_success: False" in text


@pytest.mark.asyncio
async def test_clean_run_stays_all_green():
    """反向对照：全成功任务仍全绿（防止修复过度翻转）。

    变异：把 clean_success 改成恒定 False（或 grade_hint 恒 has-failures）→ 红。
    """
    class _OkExecutor(Executor):
        async def _execute_step_once(
            self, step, plan, history, organization_id, user_id, enable_thinking,
            error_context="", ctx=None,
        ):
            if ctx is not None:
                ctx.complete_step(step.id, step.description, step.tool_hint, "completed", result="ok")
            yield AgentEvent(type="chunk", content="ok", plan_id=plan.id, plan_step_id=step.id)

    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    executor = _OkExecutor(MagicMock(), cfg, _memory(), execution_mode="serial")
    ctx = ExecutionContext(query="q-293-ok")
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="第一步", tool_hint="t")])

    [e async for e in executor.execute(plan, organization_id=1, ctx=ctx)]

    assert not ctx.failures
    report = build_run_report(ctx, final_output="答案")
    assert report["evaluation"]["clean_success"] is True
    assert report["evaluation"]["grade_hint"] == "ok"
    assert "FAILURE:" not in render_run_report_text(report)


# ── ④ 端到端：工具必失败任务 → run_report 全维度如实 ────────────────────────


@pytest.mark.asyncio
async def test_tool_always_fails_task_reports_failure_every_dimension(monkeypatch):
    """端到端判别用例：真 executor + 真注册表 + 必失败的 web_search。

    修复前：tool_result（非 tool_error）+ findings 记 high + failures 空 +
    grade ok + clean_success True（见 issue #293 复现存档）。
    修复后必须：tool_error、无 high finding、单一 failed StepRecord（无假
    completed）、failures 非空、grade has-failures、clean_success False。

    变异：M1（工具还原字符串）→ tool_result 复发 → 红；
          M3（删终态记账）→ failures 空 → 红；
          M5（attempt 尾部恢复无条件 completed）→ 出现假 completed 记录 → 红。
    """
    _failing_web_search_env(monkeypatch, RuntimeError("boom"))
    client = _fake_llm_toolclient("web_search", '{"query": "q"}')
    executor = Executor(
        client,
        AgentConfig(enable_planning=False, enable_reflection=False),
        _memory(),
        execution_mode="serial",
    )
    ctx = ExecutionContext(query="q-293-e2e")
    step = PlanStep(id="s1", description="搜索外部资料", tool_hint="web_search")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [e async for e in executor.execute(plan, organization_id=1, ctx=ctx)]

    types = [e.type for e in events]
    assert "tool_error" in types, f"工具失败必须以 tool_error 暴露: {types}"
    assert "tool_result" not in types, f"失败不得伪装为 tool_result: {types}"

    assert ctx.findings == [], "错误串不得作为 high-confidence finding 入账"
    assert step.status == "failed"

    records = [r for r in ctx.completed_steps if r.step_id == "s1"]
    assert [r.status for r in records] == ["failed"], (
        f"终态应恰有一条 failed 记录（无假 completed），实际 {[r.status for r in records]}"
    )
    assert ctx.failures and "Web search error" in ctx.failures[0], ctx.failures

    report = build_run_report(ctx, final_output="答案")
    ev = report["evaluation"]
    assert ev["clean_success"] is False
    assert ev["grade_hint"] == "has-failures"
    assert ev["failures"], "evaluation.failures 必须如实携带失败"
    assert report["steps"][0]["status"] == "failed"
    assert report["tool_calls"][0]["status"] == "failed"
    assert report["tool_calls"][0]["error"], "tool_calls 必须携带错误文本"
    text = render_run_report_text(report)
    assert "FAILURE:" in text and "[failed]" in text


# ── ⑤ R1（#293 评审核实残余，一行级）：tool_not_allowed attempt 不得写假 completed ──


@pytest.mark.asyncio
async def test_tool_not_allowed_attempt_records_single_failed_no_fake_completed():
    """R1：受限上下文（tool_tags）里模型发出未列名工具——执行层拒绝并如实发
    tool_error（fatal 分类，不重试），但该分支此前未设 attempt_error_detail →
    _execute_step_once 尾部照写一条"completed"假记录：同一 attempt 出现
    completed+failed 双记录，报告自相矛盾。

    修复：分支补 attempt_error_detail（对齐 serialization_error / ToolResult
    失败 / 异常三条既有分支）→ 该 attempt 只留重试尽头的终态 failed 一条。

    变异 M7（删 tool_not_allowed 分支的 attempt_error_detail 赋值）→
    记录变 ["completed", "failed"] → 本用例红。
    """
    cfg = AgentConfig(
        enable_planning=False, enable_reflection=False,
        tool_tags=["search", "analysis"],  # delegate_subtask 不在白名单
        max_tool_calls_per_turn=3,
    )
    client = _fake_llm_toolclient("delegate_subtask", "{}")
    executor = Executor(client, cfg, _memory(), execution_mode="serial")
    ctx = ExecutionContext(query="q-293-r1")
    step = PlanStep(id="s1", description="受限步骤", tool_hint="delegate_subtask")
    plan = Plan(id="p1", goal="g", steps=[step])

    events = [e async for e in executor.execute(plan, organization_id=1, ctx=ctx)]

    assert any(
        e.type == "tool_error" and e.error_type == "tool_not_allowed" for e in events
    ), f"拒绝执行必须如实发 tool_error: {[e.type for e in events]}"

    records = [r for r in ctx.completed_steps if r.step_id == "s1"]
    assert [r.status for r in records] == ["failed"], (
        f"同一 attempt 不得出现 completed+failed 双记录，实际 {[r.status for r in records]}"
    )
    assert step.status == "failed"
    assert ctx.failures and "不在当前上下文允许的工具集内" in ctx.failures[0], ctx.failures
