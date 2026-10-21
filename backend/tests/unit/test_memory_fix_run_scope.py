"""M1 修复验证：工作记忆的步骤结果必须按 run 隔离。

修复前的真实故障（回归点，本文件前两个测试在修复前会失败）：
- 同一会话第 2 轮复用了第 1 轮的步骤 id（"s1"/"s2" 是计划内序号），
  第 2 轮的读取会命中第 1 轮遗留的记录；
- 失败的步骤不会被 executor 写入，于是第 1 轮同名步骤的"成功"结果
  会被当作本轮结果注入下游步骤的提示词（静默假阴性）。
"""

import uuid
from unittest.mock import MagicMock

from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.services.memory_service import WorkingMemory


def _bridge(
    agent_id: str | None = None,
    organization_id: int = 1,
    user_id: int = 0,
    **kwargs,
) -> AgentMemoryBridge:
    agent_id = agent_id or f"fix-m1-{uuid.uuid4().hex[:8]}"
    return AgentMemoryBridge(
        agent_id=agent_id, organization_id=organization_id, user_id=user_id, **kwargs
    )


class TestRunScopedStepResults:
    def test_fresh_run_does_not_see_previous_run_results(self):
        """run A 写入的 "s1" 不得被同一会话的 run B 读到（修复前会读到）。"""
        namespace = f"fix-m1-{uuid.uuid4().hex[:8]}"
        run_a = _bridge(namespace)
        run_a.store_step_result(
            "s1",
            {
                "description": "查询财务数据",
                "status": "completed",
                "result": "run A 的成功结果",
            },
        )

        run_b = _bridge(namespace)
        assert run_b.get_all_results() == {}, "新 run 不得看到上一轮的中间结果"
        assert run_b.get_step_result("s1") is None

    def test_failed_step_in_new_run_does_not_echo_old_success(self):
        """本轮 "s1" 失败（executor 不写入）时，下游步骤不得收到上一轮同名成功结果。"""
        namespace = f"fix-m1-{uuid.uuid4().hex[:8]}"
        run_a = _bridge(namespace)
        run_a.store_step_result(
            "s1",
            {
                "description": "查询销售额",
                "status": "completed",
                "result": "旧一轮的成功数据",
            },
        )

        # run B：模拟执行器在步骤失败时不调用 store_step_result，直接构造下游提示词
        run_b = _bridge(namespace)
        executor = Executor(MagicMock(), AgentConfig(), run_b)
        injected = executor._get_previous_results(["s1"])

        assert injected == "", f"不得注入上一轮的结果，实际: {injected!r}"
        assert "旧一轮的成功数据" not in injected

    def test_same_run_dependency_injection_still_works(self):
        """回归保护：同一 run 内的前置步骤结果仍要注入下游提示词。"""
        bridge = _bridge()
        bridge.store_step_result(
            "s1",
            {
                "description": "查询销售额",
                "status": "completed",
                "result": "本轮销售数据",
            },
        )
        executor = Executor(MagicMock(), AgentConfig(), bridge)
        injected = executor._get_previous_results(["s1"])
        assert "本轮销售数据" in injected

    def test_reset_working_starts_new_run_scope(self):
        """reset_working() 清空工作记忆并开启新 run：旧结果在新 run 中不可见。"""
        bridge = _bridge()
        bridge.store_step_result("s1", {"status": "completed", "result": "旧结果"})
        bridge.reset_working()

        assert bridge.get_all_results() == {}
        # 本轮 "s1" 即使失败不写入，也不得读回旧结果
        assert bridge.get_step_result("s1") is None

    def test_reset_working_then_store_reads_new_value(self):
        """reset 后同一 bridge 写入的新结果仍可正常读取。"""
        bridge = _bridge()
        bridge.store_step_result("s1", {"status": "completed", "result": "旧结果"})
        bridge.reset_working()
        bridge.store_step_result("s1", {"status": "completed", "result": "新结果"})

        result = bridge.get_step_result("s1")
        assert result is not None
        assert result["result"] == "新结果"


class TestWorkingMemoryRawApi:
    """底层 API 兼容性：未传 run_id 时保持旧行为。"""

    def test_legacy_calls_without_run_id_unchanged(self):
        wm = WorkingMemory()
        wm.set_result("step-1", {"answer": "42"})
        assert wm.get_result("step-1") == {"answer": "42"}
        assert wm.get_results() == {"step-1": {"answer": "42"}}

    def test_run_scoped_reads_are_isolated(self):
        wm = WorkingMemory()
        wm.set_result("s1", {"result": "A"}, run_id="run-a")
        assert wm.get_result("s1", run_id="run-a") == {"result": "A"}
        assert wm.get_result("s1", run_id="run-b") is None
        assert wm.get_results(run_id="run-b") == {}
        assert wm.get_results(run_id="run-a") == {"s1": {"result": "A"}}
        # 不传 run_id 时保持旧行为（可见全部）
        assert wm.get_results() == {"s1": {"result": "A"}}

    def test_unscoped_overwrite_clears_run_stamp(self):
        """无 run 归属的写入覆盖后，旧 run 的读取不得再命中该 key。"""
        wm = WorkingMemory()
        wm.set_result("s1", {"result": "A"}, run_id="run-a")
        wm.set_result("s1", {"result": "anon"})
        assert wm.get_result("s1") == {"result": "anon"}
        assert wm.get_result("s1", run_id="run-a") is None

    def test_clear_wipes_run_stamps(self):
        wm = WorkingMemory()
        wm.set_result("s1", {"result": "A"}, run_id="run-a")
        wm.clear()
        assert wm.get_results() == {}
        assert wm.get_result("s1", run_id="run-a") is None


class TestNamespaceScoping:
    """命名空间：默认 agent_id 退化为每请求独立；显式 session 与 service.py 约定一致。"""

    def test_default_agent_id_no_longer_shared_across_requests(self):
        b1 = _bridge("default", organization_id=7, user_id=42)
        b2 = _bridge("default", organization_id=7, user_id=42)
        assert b1.system is not b2.system, "默认 agent_id 不得让同一用户所有会话共享记忆系统"
        assert b1.system.agent_id.startswith("7:42:default:")
        assert b1.system.agent_id != b2.system.agent_id

    def test_explicit_session_scope_shared_and_matches_convention(self):
        """显式 session_id → 命名空间与 service.py 的 "<org>:<user>:session:<id>" 约定一致。"""
        b1 = _bridge("whatever", session_id="sess-abc")
        b2 = _bridge("whatever", session_id="sess-abc")
        assert b1.system is b2.system
        assert b1.system.agent_id == "1:0:session:sess-abc"

    def test_explicit_agent_id_shared_across_bridges(self):
        namespace = f"fix-m1-{uuid.uuid4().hex[:8]}"
        assert _bridge(namespace).system is _bridge(namespace).system
