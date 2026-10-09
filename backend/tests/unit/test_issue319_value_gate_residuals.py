"""#319：值级漂移残余 —— config 快照闸门（读/写同源）+ run_report 消费侧防线。

三条全链证据（修前 baseline 落档 `_orchestrate/round2/_rv319_evidence/`）：
- chain B（值级）：快照 `config.max_iterations="很多"` → resume 在 executor
  `"很多" > 0` 比较处 `TypeError` → 错误兜底把坏 config **原样回写** →
  每次 resume 复炸（与 #287 artifacts 毒丸同族，PR §7 已披露、本批收口）；
- chain B'（容器级）：`config="boom"` → resume 读取点 `from_dict("boom")`
  `.items()` AttributeError（未写回，但每次 resume 必炸）；
- F-CRIT-1 消费点直证：`build_run_report` 对 `review="boom"` / `quality_gate=
  "boom"`（`.get`）与 `fatal_issues=123`（`list()`）均直接抛——闸门保证常态
  形状，消费侧防线保证**绕过闸门**直接构造的 ctx 也不炸（防御纵深的第二层）。
"""

import logging

from app.agent.config import _CONFIG_FIELD_SPECS, AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.run_report import build_run_report

# ── config 闸门：读侧（from_dict）────────────────────────────────────────────


def test_config_field_specs_cover_all_dataclass_fields():
    """冻结：闸门表必须与 dataclass 字段集**全等**。

    变异点：新增 dataclass 字段但不登记 spec（该字段的读侧过滤/写侧规整
    同时失效）→ 本断言红；反向（表里有已删字段）同红。
    """
    assert set(_CONFIG_FIELD_SPECS) == set(AgentConfig.__dataclass_fields__)


def test_config_to_dict_keyset_frozen():
    """冻结：to_dict 键集 = 全字段 − {planning_mode}（planning_mode 不进
    to_dict 是既有形态——实验字段，快照/Redis 都不持久化它；不许随手增删）。"""
    assert set(AgentConfig().to_dict()) == set(AgentConfig.__dataclass_fields__) - {
        "planning_mode"
    }


def test_config_read_gate_degrades_drifted_values(caplog):
    """读侧：坏值降级为默认（不传字段 = 数据类默认值）+ warning 留痕；
    容器级坏值（非映射）整取默认配置——resume / 会话加载的读取点不得被炸。

    修前红基线：`from_dict({"max_iterations": "很多"})` 照常构造出坏 config
    （无类型校验），resume 随后在 executor 比较处 TypeError；`from_dict("boom")`
    直接 AttributeError（chain B'）。
    """
    with caplog.at_level(logging.WARNING):
        cfg = AgentConfig.from_dict({
            "max_iterations": "很多",
            "temperature": "热",
            "enable_tools": "yes",
            "tool_tags": ["a", 3],
            "model": 7,
            "max_total_tokens": "无限",
        })
    assert cfg.max_iterations == 20 and isinstance(cfg.max_iterations, int)
    assert cfg.temperature == 0.1
    assert cfg.enable_tools is True
    assert cfg.tool_tags is None
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.max_total_tokens == 0
    assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕"

    with caplog.at_level(logging.WARNING):
        degraded = AgentConfig.from_dict("boom")
        degraded_list = AgentConfig.from_dict([1, 2])
    assert degraded == AgentConfig(), "容器级坏值 → 整取默认（不得炸读取点）"
    assert degraded_list == AgentConfig()


# ── config 闸门：写侧（to_dict）──────────────────────────────────────────────


def test_config_write_gate_never_persists_drifted_values(caplog):
    """写侧：运行态里万一存在坏值（外部写入者/未来 bug）→ to_dict 落盘前规整，
    坏值绝不落回快照（错误兜底回写 = 毒丸自续的正是这条路径）；坏值写出的字典
    再读回 = 默认值（自愈，不复炸）。

    变异点：to_dict 退回裸 dump → 坏值原样落盘 → 本用例红。
    """
    cfg = AgentConfig()
    cfg.max_iterations = "很多"          # type: ignore[assignment] — 模拟运行态漂移
    cfg.enable_reflection = "nope"       # type: ignore[assignment]
    with caplog.at_level(logging.WARNING):
        dumped = cfg.to_dict()
    assert dumped.get("max_iterations") != "很多"
    assert "max_iterations" not in dumped, "坏值删键落盘（缺失 = 读侧取默认）"
    assert "enable_reflection" not in dumped
    healed = AgentConfig.from_dict(dumped)
    assert healed.max_iterations == 20 and healed.enable_reflection is True
    assert any("值级漂移" in r.message for r in caplog.records)


def test_config_gate_legal_values_pass_through_byte_for_byte():
    """误伤对照：合法值（含边界：整数 0、域外负数、None 可空字段、合法列表）
    逐字节透传——闸门只看类型不看域，不做值改写。"""
    legit = {
        "model": "m",
        "temperature": 0,          # int 0 是合法 number（非 bool）
        "max_iterations": -3,      # 域外但类型合法：透传（0/负值 = 不限制语义）
        "enable_skills": False,
        "tool_tags": ["kb", "web"],
        "system_prompt_override": None,
        "personality": "precise",
        "timeout": 30,
    }
    cfg = AgentConfig.from_dict(legit)
    assert cfg.max_iterations == -3 and cfg.temperature == 0
    assert cfg.tool_tags == ["kb", "web"] and cfg.system_prompt_override is None
    assert cfg.personality == "precise" and cfg.timeout == 30
    # 全默认配置的完整 dump 逐键往返（fresh 配置 = 全合法域；键集不萎缩）
    fresh_dump = AgentConfig().to_dict()
    assert set(fresh_dump) == set(AgentConfig.__dataclass_fields__) - {"planning_mode"}
    assert AgentConfig.from_dict(fresh_dump) == AgentConfig()


# ── run_report 消费侧防线（F-CRIT-1 直证形态）───────────────────────────────


def test_run_report_consumer_defense_never_raises():
    """消费侧防线：build_run_report 对**绕过闸门**直接构造的坏形状 artifacts
    不炸——非映射按"无记录"（与 `or {}` 同语义）、非列表 fatal_issues 按空
    （与 `or []` 同语义）。

    修前直证：`review="boom"` / `quality_gate="boom"` → `"boom".get`
    AttributeError；`fatal_issues=123` → `list(123)` TypeError。合法值不受
    防线影响（reject 照常翻转 clean_success）。

    变异点：去掉 isinstance 防线（还原 `or {}` 直取）→ 前两段以对应异常红。
    """
    ctx = ExecutionContext(query="q")
    ctx.artifacts["review"] = "boom"
    ctx.artifacts["quality_gate"] = "boom"
    report = build_run_report(ctx, final_output="答案")
    assert report["evaluation"]["review_recommendation"] is None
    assert report["evaluation"]["fatal_issues"] == []
    # 无记录 → 默认语义（与 "质检/审查从未运行" 的既有投影一致）
    assert report["evaluation"]["clean_success"] is True

    ctx2 = ExecutionContext(query="q")
    ctx2.artifacts["quality_gate"] = {"passed": True, "fatal_issues": 123}
    assert build_run_report(ctx2)["evaluation"]["fatal_issues"] == []

    ctx3 = ExecutionContext(query="q")
    ctx3.artifacts["review"] = {"recommendation": "reject"}
    assert build_run_report(ctx3)["evaluation"]["clean_success"] is False, (
        "合法值不受防线影响：reject 照常翻转"
    )
