"""issue #204（轨迹设施收尾）：StepRecord / Decision 墙钟时间戳与触发步骤归属。

背景：完整轨迹设施的统一属架构决策（另开 ADR），本 issue 只收两个具体缺口：

- `StepRecord` 只有 `duration_ms`（`time.perf_counter` 差值，进程内相对量），
  没有墙钟锚点——回放 / run report 无法把步骤定位到真实时间轴；
- `Decision` 无时间戳，且 `triggered_by` 全仓约 15 个调用点从未赋值——
  决策无法归属到触发它的步骤。

写侧契约（`to_dict()` → checkpoint 超集 → 回放 timeline 三层逐字对齐）：

- `steps` 嵌套新增 `started_at` / `finished_at`（epoch 秒；运行态写入路径只会写
  已知值，`finished_at=None` 仅可能来自旧快照/外部输入）；
- `decisions` 嵌套新增 `ts`（epoch 秒）；
- **顶层键集不变**（replay / SSE execution_context 事件的既有契约，见
  `test_checkpoint_roundtrip._TO_DICT_KEYS`）；
- 旧 checkpoint（无这些嵌套键）解析不得炸：`started_at` / `ts` 缺省 0.0
  （未知哨兵），`finished_at` 缺省 None——绝不用当前时间伪造历史。

变异点（逐条对应用例）：

- M1 `complete_step` 不打点（started_at/finished_at 恒 0/None）→
  `test_complete_step_stamps_wall_clock_interval` 红；
- M2 started_at 不按 duration 回推（直接等于 finished_at）→ 同用例区间断言红；
- M3 `record_decision` 不填 triggered_by / 不认显式入参 → 对应用例红；
- M4 `to_dict` 漏投影新键 → `test_to_dict_projects_nested_timestamps_*` 红；
- M5 `from_checkpoint_dict` 对缺键用 `data["..."]` 直取 → 旧 checkpoint 用例炸；
- M6 用 `now()` 填补旧快照缺失时间戳（伪造历史）→ 0.0 哨兵断言红。
"""

import time

import pytest

from app.agent.exec_context import ExecutionContext

# 墙钟边界容差：实现用 datetime.now().timestamp()（微秒分辨率，向下截断），
# 用例锚点用 time.time()——两者在 1µs 量级可能交叉，取 1ms 容差仍能钉住
# "落在调用窗口内"的语义。
_CLOCK_EPS = 1e-3

# to_dict() 的既有顶层键集（与 test_checkpoint_roundtrip._TO_DICT_KEYS 同源）：
# 本 issue 只允许在嵌套层加键，顶层一个都不许动。
_TO_DICT_TOP_LEVEL_KEYS = {
    "task_id", "query", "goal", "plan_summary", "steps_completed", "steps",
    "findings", "decisions", "failures", "duration_ms", "total_tokens",
    "progress", "start_time",
}


# ── 打点语义：complete_step 的墙钟区间 ───────────────────────────────────────


def test_complete_step_stamps_wall_clock_interval():
    """complete_step 在收尾时刻打点：finished_at = now，started_at = now - duration。

    变异点 M1/M2：不打点 → finished_at 为 None；不按 duration 回推 →
    started_at == finished_at（区间断言红）。duration 来自 perf_counter 差值，
    回推墙钟起点忽略步骤内的时钟漂移，属可接受近似（docstring 语义）。
    """
    ctx = ExecutionContext(query="q")
    # 把构造时刻推远：杀死"用 ctx.start_time 代替实时打点"的变异——它在
    # 1ms 容差内也能过，但距真实调用窗口已 60s。
    ctx.start_time = time.time() - 60
    before = time.time()
    ctx.complete_step(
        "s1", "检索资料", "search_knowledge_base", "completed",
        result="ok", duration=1250.0,
    )
    after = time.time()

    rec = ctx.completed_steps[0]
    assert rec.finished_at is not None, "收尾必须打墙钟点（M1）"
    assert before - _CLOCK_EPS <= rec.finished_at <= after + _CLOCK_EPS, (
        "finished_at 必须落在调用前后之间"
    )
    assert rec.started_at == pytest.approx(rec.finished_at - 1.25, rel=0, abs=1e-3), (
        "started_at 必须按 duration（毫秒）回推（M2）"
    )
    assert before - 1.25 - _CLOCK_EPS <= rec.started_at <= after - 1.25 + _CLOCK_EPS


def test_complete_step_zero_duration_collapses_interval_honestly():
    """duration 未知（默认 0）→ 区间折叠为一点，而不是编造一个起点。"""
    ctx = ExecutionContext(query="q")
    ctx.complete_step("s1", "直答", None, "completed", result="ok")

    rec = ctx.completed_steps[0]
    assert rec.finished_at is not None
    assert rec.started_at == pytest.approx(rec.finished_at, rel=0, abs=1e-9)
    assert rec.duration_ms == 0.0


# ── 打点语义：record_decision 的 ts 与 triggered_by 归属 ─────────────────────


def test_record_decision_stamps_ts_and_inherits_current_step():
    """决策打墙钟点；triggered_by 缺省用 current_step_id 填充（M3）。"""
    ctx = ExecutionContext(query="q")
    ctx.current_step_id = "s7"
    # 同上：构造时刻推远 60s，杀死"用 ctx.start_time 打点"的变异。
    ctx.start_time = time.time() - 60
    before = time.time()
    ctx.record_decision("execute", "retry_step", "第一次超时")
    after = time.time()

    d = ctx.decisions[0]
    assert before - _CLOCK_EPS <= d.ts <= after + _CLOCK_EPS, "决策必须带墙钟时间戳"
    assert d.triggered_by == "s7", "缺省由 current_step_id 填充（此前全仓从未赋值）"


def test_record_decision_explicit_triggered_by_wins():
    """显式 triggered_by 覆盖 current_step_id（调用方知道真实触发者时）。"""
    ctx = ExecutionContext(query="q")
    ctx.current_step_id = "s7"
    ctx.record_decision("reflect", "finalize", "结论", triggered_by="planner")
    assert ctx.decisions[0].triggered_by == "planner"


def test_record_decision_without_current_step_leaves_triggered_by_none():
    """尚未派发过任何步骤（首轮 plan 之前）→ None，不编造归属。"""
    ctx = ExecutionContext(query="q")
    ctx.record_decision("plan", "structured", "生成计划")
    assert ctx.decisions[0].triggered_by is None


# ── 序列化：to_dict 嵌套新键、顶层键集冻结 ──────────────────────────────────


def test_to_dict_projects_nested_timestamps_without_changing_top_level_keys():
    """to_dict 在 steps/decisions 嵌套层投影新键；顶层键集一个都不许动（M4）。"""
    ctx = ExecutionContext(query="q")
    ctx.complete_step("s1", "第一步", "t", "completed", duration=10.0)
    ctx.record_decision("execute", "begin_step", "开始")

    data = ctx.to_dict()
    assert set(data) == _TO_DICT_TOP_LEVEL_KEYS, "顶层键集冻结（replay / SSE 契约）"
    assert data["steps"][0]["started_at"] == ctx.completed_steps[0].started_at
    assert data["steps"][0]["finished_at"] == ctx.completed_steps[0].finished_at
    assert data["decisions"][0]["ts"] == ctx.decisions[0].ts
    assert data["decisions"][0]["triggered_by"] == ctx.decisions[0].triggered_by


# ── 向后兼容：旧 checkpoint 解析 + 往返保真 ─────────────────────────────────


def test_checkpoint_roundtrip_preserves_timestamps():
    """三个新键随 checkpoint 往返逐字保真（from_checkpoint_dict 漏读 → 红）。"""
    ctx = ExecutionContext(query="q")
    ctx.current_step_id = "s1"
    ctx.complete_step("s1", "第一步", "t", "completed", duration=250.0)
    ctx.record_decision("execute", "begin_step", "开始")

    restored = ExecutionContext.from_checkpoint_dict(ctx.to_checkpoint_dict())

    rec, d = restored.completed_steps[0], restored.decisions[0]
    assert rec.started_at == ctx.completed_steps[0].started_at
    assert rec.finished_at == ctx.completed_steps[0].finished_at
    assert d.ts == ctx.decisions[0].ts
    assert d.triggered_by == "s1", "归属信息同样随快照恢复"


def test_legacy_checkpoint_without_nested_timestamps_parses_with_unknown_sentinels():
    """旧 checkpoint（无新嵌套键）解析不得炸；未知时间戳用 0.0/None 哨兵。

    变异点 M5：直取 data["started_at"] / d["ts"] → KeyError（旧快照炸 resume）；
    M6：用当前时间填补 → 0.0 断言红（伪造历史）。
    """
    legacy_payload = {
        "v": 1,
        "task_id": "t-legacy",
        "query": "q",
        "steps": [
            {
                "step_id": "s1", "description": "旧步骤", "tool_used": None,
                "status": "completed", "result_summary": "ok", "error": None,
                "duration_ms": 10.0, "tokens_used": 1,
            },
        ],
        "decisions": [
            {"phase": "execute", "action": "begin_step", "reasoning": "旧决策",
             "triggered_by": None},
        ],
    }

    restored = ExecutionContext.from_checkpoint_dict(legacy_payload)

    rec = restored.completed_steps[0]
    assert rec.started_at == 0.0 and rec.finished_at is None, (
        "旧快照时间戳未知：started_at 0.0 / finished_at None（不得编造）"
    )
    d = restored.decisions[0]
    assert d.ts == 0.0, "旧快照决策时间戳未知必须是 0.0 哨兵"

    # 未知哨兵必须原样随下一次快照写回（形状稳定，不引入 False 的新事实）
    rewritten = restored.to_dict()
    assert rewritten["steps"][0]["started_at"] == 0.0
    assert rewritten["steps"][0]["finished_at"] is None
    assert rewritten["decisions"][0]["ts"] == 0.0
