"""HITL 审批门 —— 模块判定 + 执行器级行为判别（无 DB）。

被测契约（app/agent/approval.py 模块 docstring + executor 的
_approval_precheck / _gate_materialization）：

- risky 模式只拦 high；always 拦一切；never 全放；
- 预检在**任何**调用执行前暂停（多调用回合先执行再暂停 = 重复副作用）；
- 决定绑定 (tool, args 指纹) 且粘滞：approve 放行、deny 结构化失败
  approval_denied（不执行）、换参数要新的决定；
- 取消旗在步间/工具前检查点收敛为 RunCancelled（不执行工具）；
- 暂停无法持久化 → 降级 approval_unavailable（绝不静默执行）。

驱动方式：假 LLM 的 chat.completions.create + 直接驱动 executor
（与 test_executor_override_and_tool_guard.py 同款），registry.execute_detailed
用 AsyncMock 替身（审批测试不碰真实工具）。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
from app.agent import approval
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolResult, tool_registry
from app.rag.retriever import HybridRetriever


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离语义记忆 embedding 的真实 RPC（与 test_agent_recovery_flow 同款：
    本机可达时 404、CI 不可达时连接失败——桩成"无网络、空向量"两种环境稳定）。"""
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


# ── 假件 ─────────────────────────────────────────────────────────────────────


def _client_with_tool_calls(calls_spec, capture: dict | None = None):
    """假 LLM：一次回复里带上给定的 tool_calls。calls_spec = [(name, args_json, id)]。"""
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        if capture is not None:
            capture.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tcs = []
        for name, args_json, cid in calls_spec:
            tc = MagicMock()
            tc.id = cid
            tc.function.name = name
            tc.function.arguments = args_json
            tcs.append(tc)
        msg.tool_calls = tcs
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _client_plain(content: str = "步骤结论"):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = content
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _mk_executor(config: AgentConfig, client) -> Executor:
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(client, config, memory)


def _cfg(**overrides) -> AgentConfig:
    base = dict(
        enable_reflection=False, enable_memory=False, enable_skills=False,
        enable_experience=False, enable_thinking=False,
    )
    base.update(overrides)
    return AgentConfig(**base)


async def _drive(gen) -> tuple[list, BaseException | None]:
    """消费生成器直到正常结束或抛出；返回 (events, 异常或None)。"""
    events: list = []
    try:
        async for ev in gen:
            events.append(ev)
    except BaseException as e:  # noqa: BLE001 — 判别用例要拿到控制流异常本身
        return events, e
    return events, None


def _arm(executor: Executor, *, outcome: str = "paused", calls: list | None = None):
    async def _check(request: approval.ApprovalRequest) -> str:
        if calls is not None:
            calls.append(request)
        return outcome

    executor.approval_check = _check


async def _run_step_once(executor: Executor, *, step_id: str = "s1"):
    step = PlanStep(id=step_id, description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    return await _drive(executor._execute_step_once(
        step=step, plan=plan, history=None, organization_id=2, user_id=0,
        enable_thinking=False,
    )), step, plan


# ── 判定表 / 纯函数（模块级契约）──────────────────────────────────────────────


class TestApprovalModule:
    def test_risk_table_and_registry_declaration(self):
        # 注册表声明优先（execute_python 三个工具在装饰器上声明了 high）
        assert approval.tool_risk_level("execute_python") == "high"
        assert approval.tool_risk_level("execute_sql") == "high"
        assert approval.tool_risk_level("mcp_call") == "high"
        # 集中判定表（未声明、命中表）
        assert approval.tool_risk_level("fetch_webpage") == "medium"
        assert approval.tool_risk_level("delegate_subtask") == "medium"
        # 表外默认 low（知识库只读面）
        assert approval.tool_risk_level("search_knowledge_base") == "low"
        assert approval.tool_risk_level("not_a_registered_tool_zzz") == "low"

    def test_registry_declared_level_beats_table(self):
        async def _h(**_):
            return "x"

        entry = tool_registry.register(
            name="zz_fake_declared_low",
            description="t",
            parameters={"type": "object", "properties": {}},
            handler=_h,
            risk_level="low",  # 即便判定表/语义想拦它，声明优先
        )
        try:
            assert entry.risk_level == "low"
            assert approval.tool_risk_level("zz_fake_declared_low") == "low"
        finally:
            tool_registry._tools.pop("zz_fake_declared_low", None)

    def test_mutating_semantic_falls_back_to_medium(self):
        async def _h(**_):
            return "x"

        tool_registry.register(
            name="zz_fake_mutating",
            description="t",
            parameters={"type": "object", "properties": {}},
            handler=_h,
            semantic={"type": "mutating", "idempotent": False, "retry_safe": False},
        )
        try:
            assert approval.tool_risk_level("zz_fake_mutating") == "medium"
        finally:
            tool_registry._tools.pop("zz_fake_mutating", None)

    def test_requires_approval_matrix(self):
        assert approval.requires_approval("never", "execute_python") is False
        assert approval.requires_approval("risky", "execute_python") is True
        assert approval.requires_approval("risky", "fetch_webpage") is False  # medium 不拦
        assert approval.requires_approval("risky", "search_knowledge_base") is False
        assert approval.requires_approval("always", "search_knowledge_base") is True

    def test_args_fingerprint_order_insensitive_and_discriminating(self):
        a = approval.args_fingerprint({"a": 1, "b": [2, 3]})
        assert a == approval.args_fingerprint({"b": [2, 3], "a": 1})
        assert a != approval.args_fingerprint({"a": 1, "b": [3, 2]})
        assert approval.args_fingerprint(None) == approval.args_fingerprint({})

    def test_find_decision_last_write_wins(self):
        args = {"code": "print(1)"}
        fp = approval.args_fingerprint(args)
        ledger = [
            {"tool_name": "execute_python", "args_hash": fp, "decision": "deny"},
            {"tool_name": "execute_python", "args_hash": fp, "decision": "approve"},
        ]
        found = approval.find_decision(ledger, "execute_python", args)
        assert found is not None and found["decision"] == "approve"
        # 不同参数 / 不同工具 → 无决定
        assert approval.find_decision(ledger, "execute_python", {"code": "print(2)"}) is None
        assert approval.find_decision(ledger, "execute_sql", args) is None
        assert approval.find_decision(None, "execute_python", args) is None

    def test_approval_expired(self):
        assert approval.approval_expired(None) is False
        assert approval.approval_expired({}) is False
        assert approval.approval_expired({"expires_at": None}) is False
        assert approval.approval_expired({"expires_at": "garbage"}) is False
        assert approval.approval_expired({"expires_at": 1e12}) is False
        assert approval.approval_expired({"expires_at": 1.0}) is True


# ── 执行器级行为 ─────────────────────────────────────────────────────────────


class TestExecutorApprovalGate:
    @pytest.mark.asyncio
    async def test_risky_mode_pauses_before_any_call_executes(self):
        """多调用回合：第二个才高危——预检必须在**第一个执行前**暂停。

        变异点：把预检挪进执行循环（边执行边查）→ execute_detailed 会被
        search_knowledge_base 调用一次 → 本用例红。
        """
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([
                ("search_knowledge_base", '{"query": "q"}', "c1"),
                ("execute_python", '{"code": "print(1)"}', "c2"),
            ]),
        )
        requests: list = []
        _arm(executor, outcome="paused", calls=requests)
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), step, _plan = await _run_step_once(executor)

        assert isinstance(exc, approval.ApprovalRequired)
        assert spy.await_count == 0, "预检暂停前不得执行任何调用"
        assert len(requests) == 1
        assert requests[0].tool_name == "execute_python"  # 第一个未决=第一个高危
        assert requests[0].risk_level == "high"
        assert requests[0].tool_args == {"code": "print(1)"}
        assert events[-1].type == "approval_required"
        assert events[-1].tool_name == "execute_python"
        assert events[-1].tool_call_id == "c2"
        assert events[-1].risk_level == "high"
        assert executor.pending_control == "approval"

    @pytest.mark.asyncio
    async def test_approve_ledger_allows_execution_without_pausing(self):
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("execute_python", '{"code": "print(1)"}', "c1")]),
        )
        requests: list = []
        _arm(executor, outcome="paused", calls=requests)
        executor.approval_decisions = [{
            "tool_name": "execute_python",
            "args_hash": approval.args_fingerprint({"code": "print(1)"}),
            "decision": "approve",
        }]
        spy = AsyncMock(return_value=ToolResult.ok(data="假执行结果"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None, "已批准的决定不得再触发暂停"
        assert requests == [], "预检不得回调（已有决定）"
        assert spy.await_count == 1
        assert any(e.type == "tool_result" for e in events)
        assert all(e.type != "approval_required" for e in events)

    @pytest.mark.asyncio
    async def test_deny_ledger_materializes_structured_failure_without_execution(self):
        """变异点：deny 分支被改成 tool_result 成功（或静默跳过）→ 本用例红。"""
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("execute_python", '{"code": "print(1)"}', "c1")]),
        )
        executor.approval_decisions = [{
            "tool_name": "execute_python",
            "args_hash": approval.args_fingerprint({"code": "print(1)"}),
            "decision": "deny",
            "note": "生产环境禁止明令",
        }]

        async def _boom(_req):
            raise AssertionError("已有 deny 决定，审批回调不得触发")

        executor.approval_check = _boom
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        assert spy.await_count == 0, "被拒绝的调用绝不执行"
        errors = [e for e in events if e.type == "tool_error"]
        assert errors and errors[0].error_type == "approval_denied"
        assert "approval_denied" in errors[0].content
        assert "生产环境禁止明令" in errors[0].content, "拒绝备注应喂给反思上下文"

    @pytest.mark.asyncio
    async def test_decision_bound_to_args_fingerprint(self):
        """换参数 = 换行动：同一工具、不同参数必须重新审批（变异点：只按工具名
        匹配 → 本用例红——第二次调用不再暂停）。"""
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("execute_python", '{"code": "print(2)"}', "c1")]),
        )
        executor.approval_decisions = [{
            "tool_name": "execute_python",
            "args_hash": approval.args_fingerprint({"code": "print(1)"}),  # 批准的是别的参数
            "decision": "approve",
        }]
        requests: list = []
        _arm(executor, outcome="paused", calls=requests)
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert isinstance(exc, approval.ApprovalRequired)
        assert spy.await_count == 0
        assert len(requests) == 1 and requests[0].tool_args == {"code": "print(2)"}

    @pytest.mark.asyncio
    async def test_cancelled_during_gate_check_raises_run_cancelled(self):
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("execute_python", "{}", "c1")]),
        )
        _arm(executor, outcome="cancelled")
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert isinstance(exc, approval.RunCancelled)
        assert spy.await_count == 0
        assert executor.pending_control == "cancel"
        assert all(e.type != "approval_required" for e in events)

    @pytest.mark.asyncio
    async def test_unavailable_degrades_to_structured_failure(self):
        """暂停无法持久化（DB 故障）→ 需审批调用一律不执行（变异点：降级放行
        → 本用例红）。"""
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("execute_python", "{}", "c1")]),
        )
        _arm(executor, outcome="unavailable")
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        assert executor.approval_degraded is True
        assert spy.await_count == 0
        errors = [e for e in events if e.type == "tool_error"]
        assert errors and errors[0].error_type == "approval_unavailable"

    @pytest.mark.asyncio
    async def test_never_mode_gates_nothing(self):
        executor = _mk_executor(
            _cfg(approval_mode="never"),
            _client_with_tool_calls([("execute_python", "{}", "c1")]),
        )

        async def _boom(_req):
            raise AssertionError("never 模式不得触发审批回调")

        executor.approval_check = _boom
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        assert spy.await_count == 1

    @pytest.mark.asyncio
    async def test_always_mode_gates_low_risk_tool(self):
        executor = _mk_executor(
            _cfg(approval_mode="always"),
            _client_with_tool_calls([("search_knowledge_base", "{}", "c1")]),
        )
        requests: list = []
        _arm(executor, outcome="paused", calls=requests)
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert isinstance(exc, approval.ApprovalRequired)
        assert spy.await_count == 0
        assert len(requests) == 1 and requests[0].tool_name == "search_knowledge_base"

    @pytest.mark.asyncio
    async def test_invalid_json_call_not_gated(self):
        """参数非法的调用不执行也不审批（既有 serialization_error 路径不受扰）。"""
        executor = _mk_executor(
            _cfg(approval_mode="always"),
            _client_with_tool_calls([("execute_python", "{oops", "c1")]),
        )
        requests: list = []
        _arm(executor, outcome="paused", calls=requests)
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        assert requests == []
        assert spy.await_count == 0
        errors = [e for e in events if e.type == "tool_error"]
        assert errors and errors[0].error_type == "serialization_error"


class TestExecutorCancelChecks:
    @pytest.mark.asyncio
    async def test_cancel_flag_blocks_tool_before_execution(self):
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("search_knowledge_base", "{}", "c1")]),
        )
        executor.cancel_check = AsyncMock(return_value=True)
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        # 步骤前检查点先触发（_execute_step_once 顶部），无论哪个检查点，语义一致：
        assert isinstance(exc, approval.RunCancelled)
        assert executor.pending_control == "cancel"
        assert spy.await_count == 0

    @pytest.mark.asyncio
    async def test_cancel_flag_blocks_between_steps(self):
        """步间检查点：s1 完成后置旗 → s2 不再派发（不发起第二次 LLM 调用）。"""
        calls = {"n": 0}

        base_client = _client_plain("结论一")

        async def _create(*, messages, tools=None, **kwargs):
            calls["n"] += 1
            return await base_client.chat.completions.create(
                messages=messages, tools=tools, **kwargs
            )

        client = MagicMock()
        client.chat.completions.create = _create
        executor = _mk_executor(_cfg(approval_mode="risky"), client)

        flags = {"cancel": False}

        async def _cc():
            return flags["cancel"]

        executor.cancel_check = _cc
        step = PlanStep(id="s1", description="一")
        step2 = PlanStep(id="s2", description="二", dependencies=["s1"])
        plan = Plan(id="p1", goal="g", steps=[step, step2])

        async def _on_step_done(_step):
            flags["cancel"] = True

        events, exc = await _drive(executor.execute(
            plan=plan, history=None, organization_id=2, user_id=0,
            enable_thinking=False, on_step_done=_on_step_done,
        ))

        assert isinstance(exc, approval.RunCancelled)
        assert calls["n"] == 1, "s2 不得再发起 LLM 调用"
        assert step.status == "completed"
        assert step2.status == "pending", "取消不篡改计划（s2 留在 pending）"
        assert executor.pending_control == "cancel"

    @pytest.mark.asyncio
    async def test_cancel_channel_failure_is_not_cancel(self):
        """取消通道故障按"未取消"处理：打不坏正常执行。"""
        async def _broken():
            raise RuntimeError("redis down")

        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("search_knowledge_base", "{}", "c1")]),
        )
        executor.cancel_check = _broken
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        assert spy.await_count == 1


class TestHumanGuidanceInjection:
    @pytest.mark.asyncio
    async def test_guidance_reaches_step_prompt(self):
        """改指令续跑：审批附加的人工指导必须进入步骤提示词（变异点：不注入
        → 本用例红）。"""
        capture: dict = {}
        executor = _mk_executor(
            _cfg(approval_mode="risky"),
            _client_with_tool_calls([("search_knowledge_base", "{}", "c1")], capture=capture),
        )
        executor.human_guidance = "请只查询 2024 年之后的文档"
        spy = AsyncMock(return_value=ToolResult.ok(data="x"))
        with patch.object(tool_registry, "execute_detailed", new=spy):
            (events, exc), _, _ = await _run_step_once(executor)

        assert exc is None
        system_content = capture["messages"][0][0]["content"]
        assert "人工指导" in system_content
        assert "请只查询 2024 年之后的文档" in system_content
