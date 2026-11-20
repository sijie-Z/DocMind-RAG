"""工作流条件分支路由判别性测试（竞品对标修复）。

现状缺陷：`workflow_engine.make_router` 直接按 `edge.label` 匹配
condition_result/route_result，label 为 None 时在路由场景可 `.lower()` 崩溃；
无匹配则静默走第一个目标——条件分支看似配置成功、实际永远走同一支。

变异点：
- 把 resolve_conditional_target 改回「默认返回 tgts[0]」→ 用例 1/2 红；
- 去掉 route_result 与 edge.target 的匹配 → 用例 3 红；
- 去掉 None 安全处理 → 用例 4 红。
"""

from __future__ import annotations

import pytest

from app.services.workflow_engine import resolve_conditional_target


def _targets(*pairs):
    return [{"target": target, "label": label, "sourceHandle": None} for target, label in pairs]


class TestConditionalTargetResolution:
    def test_condition_result_matches_edge_label(self):
        targets = _targets(("n-default", "default"), ("n-translate", "translate"))
        state = {"context": {"condition_result": "translate"}}
        assert resolve_conditional_target(targets, state) == "n-translate"

    def test_no_match_fails_closed_instead_of_silent_first(self):
        # 没有 default 边、也没有任何 label 命中时，不能静默走第一条
        targets = _targets(("n-a", "alpha"), ("n-code", "code"))
        state = {"context": {"condition_result": "analyze"}}
        with pytest.raises(ValueError, match="条件分支"):
            resolve_conditional_target(targets, state)

    def test_route_result_can_match_edge_target(self):
        # RouterNodeExecutor 的 route_result 是 routes[].target（节点 id），
        # 不是条件名；必须能直接按目标 id 命中。
        targets = _targets(("n-a", None), ("n-b", None))
        state = {"context": {"route_result": "n-b"}}
        assert resolve_conditional_target(targets, state, "router") == "n-b"

    def test_stale_router_result_does_not_poison_downstream_condition(self):
        """评审 A 回归：Router 未命中产出 route_result="default" 残留在共享
        context；下一个 ConditionNode 产出 translate 时必须走 translate 分支，
        不能被残留的 default 抢走。"""
        targets = _targets(("n-default", "default"), ("n-translate", "translate"))
        state = {"context": {"route_result": "default", "condition_result": "translate"}}
        assert resolve_conditional_target(targets, state, "condition") == "n-translate"

    def test_stale_router_target_does_not_poison_downstream_condition(self):
        """更一般情形：Router 命中的节点 id 残留，也不能覆盖 Condition 结果。"""
        targets = _targets(("n-a", "alpha"), ("n-b", "beta"))
        state = {"context": {"route_result": "n-a", "condition_result": "beta"}}
        assert resolve_conditional_target(targets, state, "condition") == "n-b"

    def test_none_label_does_not_crash(self):
        # Router 场景：label 全为 None 时只按 edge.target 命中，不能 .lower() 崩溃
        targets = _targets(("n-a", None), ("n-b", None))
        state = {"context": {"route_result": "n-a", "condition_result": None}}
        assert resolve_conditional_target(targets, state, "router") == "n-a"

    def test_none_label_reaches_label_comparison_without_crash(self):
        """评审 B 变异 M4：恢复 label.lower() 会让 None label 崩溃。
        前一条 None 测试被 target 短路，这里强制走到 label 比较路径。"""
        targets = _targets(("n-a", None), ("n-b", "beta"))
        state = {"context": {"condition_result": "beta"}}
        assert resolve_conditional_target(targets, state, "condition") == "n-b"

    def test_explicit_default_edge_when_no_specific_match(self):
        targets = _targets(("n-default", "default"), ("n-code", "code"))
        state = {"context": {"condition_result": "unknown"}}
        assert resolve_conditional_target(targets, state) == "n-default"
