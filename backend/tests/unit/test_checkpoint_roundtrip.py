"""issue #201 最小版：checkpoint 快照的序列化往返（保真 / 契约冻结 / 安全降级）。

- ctx / plan 的 checkpoint 形状是 to_dict() / _save_plan payload 的**超集**：
  补 artifacts（迭代预算、质检/审查结论）、current_step_id、result /
  error_context / retry_count —— 缺了它们，resume 会静默重置预算、丢步骤结果
  （本文件逐项钉住这些字段的往返保真）。
- to_dict() 的键集是既有 replay / SSE execution_context 契约，不许动
  （本文件冻住它；变异点：往 to_dict 里加/删键 → 红）。
- parse_snapshot 对损坏 / 未知版本一律返回 None（安全降级为"无快照"）。
"""

import logging

from app.agent.checkpoint import (
    CHECKPOINT_VERSION,
    HISTORY_CAP,
    PARTIAL_OUTPUT_CAP,
    build_envelope,
    parse_snapshot,
)
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan, PlanStep

# to_dict() 的既有键集（replay / SSE 的消费面）：refactor 时不许增删。
_TO_DICT_KEYS = {
    "task_id", "query", "goal", "plan_summary", "steps_completed", "steps",
    "findings", "decisions", "failures", "duration_ms", "total_tokens",
    "progress", "start_time",
}


def _rich_ctx() -> ExecutionContext:
    ctx = ExecutionContext(query="分析一下", task_id="t-abc")
    ctx.goal = "目标"
    ctx.plan_summary = "3 个步骤"
    ctx.current_step_id = "s2"
    ctx.total_tokens = 987
    ctx.progress = 0.66
    ctx.duration_ms = 1234.5
    ctx.artifacts["iterations_used"] = 2
    ctx.artifacts["quality_gate"] = {"passed": False, "fatal_issues": ["空结果"]}
    ctx.artifacts["review"] = {"recommendation": "revise", "issue_count": 1}
    ctx.add_finding("s1", "找到了 X", tool="search_knowledge_base", confidence="high")
    ctx.record_decision("execute", "begin_step", "开始执行 s1")
    ctx.complete_step("s1", "第一步", "search_knowledge_base", "completed",
                      result="结果正文", duration=42.0, tokens=321)
    ctx.complete_step("s2", "第二步", None, "failed", error="boom")
    return ctx


def test_execution_context_checkpoint_roundtrip_preserves_everything():
    """变异点：from_checkpoint_dict 漏掉 artifacts / current_step_id / total_tokens
    任一 → 对应断言红（resume 后预算口径断裂、迭代计数归零）。"""
    ctx = _rich_ctx()
    payload = ctx.to_checkpoint_dict()

    restored = ExecutionContext.from_checkpoint_dict(payload)

    assert restored.task_id == "t-abc"
    assert restored.query == "分析一下"
    assert restored.goal == "目标"
    assert restored.plan_summary == "3 个步骤"
    assert restored.current_step_id == "s2"
    assert restored.total_tokens == 987
    assert restored.artifacts == ctx.artifacts, "artifacts（迭代预算/质检/审查）必须随快照往返"
    assert [s.step_id for s in restored.completed_steps] == ["s1", "s2"]
    assert restored.completed_steps[0].result_summary == "结果正文"
    assert restored.completed_steps[0].tokens_used == 321
    assert restored.completed_steps[1].status == "failed"
    assert restored.completed_steps[1].error == "boom"
    assert restored.findings[0].content == "找到了 X"
    assert restored.findings[0].confidence == "high"
    assert restored.decisions[0].action == "begin_step"
    # complete_step 会按 failed 追加 failures；恢复不得二次追加（列表不重复）
    assert len(restored.failures) == 1
    assert restored.start_time == ctx.start_time


def test_to_dict_contract_keyset_is_frozen():
    """契约不回归（报告测试 8）：to_checkpoint_dict 是超集，to_dict 本体不动。"""
    ctx = ExecutionContext(query="q")
    assert set(ctx.to_dict().keys()) == _TO_DICT_KEYS
    extra = set(ctx.to_checkpoint_dict().keys()) - _TO_DICT_KEYS
    assert extra == {"v", "current_step_id", "artifacts"}, "checkpoint 只允许在 to_dict 之上做加法"


def test_plan_checkpoint_roundtrip_preserves_results_and_retry():
    """变异点：to_checkpoint_dict 退回 _save_plan 的 payload（不含 result/
    error_context/retry_count）→ result 断言红（resume 不知哪些步已完成）。"""
    plan = Plan(id="p9", goal="g", reasoning="r",
                completed_steps=1, failed_steps=1, created_at=111.5)
    plan.steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1",
                 retry_count=1, parallel_group=1, fallback_tool="fb"),
        PlanStep(id="s2", description="二", status="failed", error_context="err",
                 dependencies=["s1"]),
        PlanStep(id="s3", description="三", dependencies=["s2"], tool_hint="t"),
    ]
    restored = Plan.from_checkpoint_dict(plan.to_checkpoint_dict())

    assert restored.id == "p9" and restored.goal == "g" and restored.created_at == 111.5
    assert restored.completed_steps == 1 and restored.failed_steps == 1
    assert [s.status for s in restored.steps] == ["completed", "failed", "pending"]
    assert restored.steps[0].result == "R1"
    assert restored.steps[0].retry_count == 1
    assert restored.steps[0].fallback_tool == "fb"
    assert restored.steps[1].error_context == "err"
    assert restored.steps[2].dependencies == ["s2"]
    assert restored.steps[2].tool_hint == "t"


def test_envelope_carries_session_and_caps_partial_and_history():
    """信封附加项 + 上限：partial_output 截到 16KB、history 只留最后 50 条。"""
    ctx = ExecutionContext(query="q")
    plan = Plan(id="p", goal="g")
    history = [{"role": "user", "content": f"m{i}"} for i in range(HISTORY_CAP + 10)]

    env = build_envelope(
        ctx=ctx, plan=plan, trigger="after_planning",
        partial_output="汉" * (PARTIAL_OUTPUT_CAP + 5),
        history=history, config=AgentConfig(),
        run_state={"replan_attempts": 1}, session_id="sess-1",
    )

    assert env["v"] == CHECKPOINT_VERSION
    assert env["session_id"] == "sess-1"
    assert env["updated_by"] == "after_planning"
    assert env["run"]["replan_attempts"] == 1
    assert len(env["partial_output"]) == PARTIAL_OUTPUT_CAP
    assert len(env["history"]) == HISTORY_CAP
    assert env["history"][-1]["content"] == f"m{HISTORY_CAP + 9}"
    assert env["config"]["enable_checkpoint"] is True
    assert env["plan"]["id"] == "p"


def test_parse_snapshot_degrades_safely_on_any_damage():
    """损坏 checkpoint 的安全降级：结构性坏数据一律 → None（绝不拿它重建状态）。
    变异点：parse_snapshot 放行未知版本/缺 ctx → 对应断言红。"""
    assert parse_snapshot(None) is None
    assert parse_snapshot("junk") is None
    assert parse_snapshot([1, 2]) is None
    assert parse_snapshot({}) is None
    assert parse_snapshot({"v": CHECKPOINT_VERSION + 1, "ctx": {}, "plan": None}) is None
    assert parse_snapshot({"v": CHECKPOINT_VERSION, "ctx": "not-a-dict"}) is None
    assert parse_snapshot({"v": CHECKPOINT_VERSION, "ctx": {}, "plan": ["garbage"]}) is None

    good = {"v": CHECKPOINT_VERSION, "ctx": {}, "plan": None}
    assert parse_snapshot(good) is good
    good_with_plan = {"v": CHECKPOINT_VERSION, "ctx": {"query": "q"}, "plan": {"id": "p"}}
    assert parse_snapshot(good_with_plan) is good_with_plan


def test_value_level_drift_is_normalized_on_read_and_write():
    """#287/R1 值级规整：合法 JSON、值类型漂移的已知键（iterations_used 是整数
    计数口径）在**读**（from_checkpoint_dict）与**写**（to_checkpoint_dict）两侧
    过同一道闸门——坏值降级为默认（删键 = 消费方 `.get(key, 0)` 的默认语义）
    并留 warning；绝不把坏值带进运行态，也绝不让它落回快照（错误兜底回写 =
    毒丸自续的正是这条路径）。

    变异点：去掉任一侧规整 → 对应断言红（读侧：坏值原样进 artifacts；写侧：
    坏值原样进快照 dict）。
    """
    # 读侧：快照里值类型漂移 → 降级；无关键原样保留
    restored = ExecutionContext.from_checkpoint_dict({
        "query": "q",
        "artifacts": {"iterations_used": "很多", "quality_gate": {"passed": True}},
    })
    assert "iterations_used" not in restored.artifacts, (
        "坏值不得进运行态（降级为默认：消费方 .get(k, 0)）"
    )
    assert restored.artifacts["quality_gate"] == {"passed": True}, "无关键原样保留"

    # 写侧：运行态里若存在坏值（未来新写入者/内存漂移）→ 落盘前规整，
    # 坏值绝不落回快照（否则错误兜底回写把毒丸续给下一次 resume）
    ctx = ExecutionContext(query="q")
    ctx.artifacts["iterations_used"] = "很多"
    ctx.artifacts["review"] = {"recommendation": "pass"}
    written = ctx.to_checkpoint_dict()["artifacts"]
    assert written.get("iterations_used") != "很多", "坏值原样回写 = 毒丸自续（必须规整）"
    assert "iterations_used" not in written
    assert written["review"] == {"recommendation": "pass"}

    # 好值往返不失真（规整不得误伤正常计数）
    ctx_ok = ExecutionContext(query="q")
    ctx_ok.artifacts["iterations_used"] = 3
    assert ctx_ok.to_checkpoint_dict()["artifacts"]["iterations_used"] == 3
    assert ExecutionContext.from_checkpoint_dict(
        {"query": "q", "artifacts": {"iterations_used": 3}}
    ).artifacts["iterations_used"] == 3


def test_artifact_consumer_key_shape_gate_read_and_write(caplog):
    """#319（F-CRIT-1 残族）：artifacts 闸门从"一张 int 键表"扩成"每个被消费键
    一条形状契约"（盘点见 exec_context 注释）：
    - review / quality_gate：run_report 以映射消费（`.get`）——非映射是
      F-CRIT-1 全链实证的毒丸形态（resume 收尾 AttributeError → 错误兜底
      回写 → 复炸）：整键删；
    - quality_gate.fatal_issues：`list(...)` 消费——非列表是 TypeError（int）
      或逐字符垃圾（str）：坏则删子键；零消费点的 passed 原样保留；
    - review.recommendation：str 契约，坏则删子键；
    - iterations_used=True/False：bool 拒斥——isinstance(True, int) 为真，
      但 JSON bool 是类型漂移，写入方只写 int，静默等价为 1/0 即"过归一化"
      （评审 MV4 缺口：bool 分支此前零用例钉住）。

    修前红基线：读侧坏 review/quality_gate 原样进运行态（F-CRIT-1 形态）、
    写侧坏值原样落回快照（毒丸自续通道）。变异点：去掉映射/子键/bool 任一段
    → 对应断言红。
    """
    with caplog.at_level(logging.WARNING):
        # 读侧：映射键非映射 → 整键删；子键坏 → 删子键；bool → 删键
        restored = ExecutionContext.from_checkpoint_dict({
            "query": "q",
            "artifacts": {
                "review": "boom",
                "quality_gate": {"passed": True, "fatal_issues": 123},
                "iterations_used": True,
            },
        })
    assert "review" not in restored.artifacts, "非映射 review = F-CRIT-1 毒丸形态（整键删）"
    assert restored.artifacts["quality_gate"] == {"passed": True}, (
        "fatal_issues 非列表 → 删子键；零消费点的 passed 原样保留"
    )
    assert "iterations_used" not in restored.artifacts, "bool 拒斥（不得静默等价为 1）"

    # 写侧：运行态坏值 → 落盘前规整（错误兜底回写 = 毒丸自续的正是这条路径）
    ctx = ExecutionContext(query="q")
    ctx.artifacts["review"] = "boom"
    ctx.artifacts["quality_gate"] = {"passed": False, "fatal_issues": "boom"}
    ctx.artifacts["iterations_used"] = False
    written = ctx.to_checkpoint_dict()["artifacts"]
    assert "review" not in written, "坏值原样回写 = 毒丸自续（整键删）"
    assert written["quality_gate"] == {"passed": False}, "坏子键不落回快照"
    assert "iterations_used" not in written

    # 合法值逐字节透传（误伤对照：全契约形态 + 域外但类型合法的值）
    legit = {
        "iterations_used": -3,
        "quality_gate": {"passed": False, "fatal_issues": ["空结果", "重复内容"]},
        "review": {"recommendation": "revise", "issue_count": 1, "high_severity_count": 0},
    }
    ctx_ok = ExecutionContext(query="q")
    ctx_ok.artifacts.update(legit)
    assert ctx_ok.to_checkpoint_dict()["artifacts"] == legit
    assert ExecutionContext.from_checkpoint_dict(
        {"query": "q", "artifacts": legit}
    ).artifacts == legit
    assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕"
