"""issue #509（P0）：工具别名审批安全。

覆盖：
- 单一 TOOL_ALIASES：别名风险等级/审批判定与规范名一致；
- 别名指向 high 工具时 ``requires_approval`` 一致，executor 审批请求落规范名；
- executor 的 ledger 与 SSE 事件使用规范名；
- skills 内部 ``execute_detailed`` 先 canonicalize，并补 disabled/审批预检；
- 旧名兼容：别名默认参数合并、显式参数优先。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
from app.agent import approval
from app.agent import executor as executor_mod
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import (
    TOOL_ALIASES,
    ToolAlias,
    ToolResult,
    canonical_tool_name,
    canonicalize_tool_call,
    tool_registry,
)


def _client_with_tool_calls(calls_spec):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
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


def _executor(cfg: AgentConfig, client) -> Executor:
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(client, cfg, memory)


async def _drive(gen):
    events = []
    try:
        async for event in gen:
            events.append(event)
    except BaseException as exc:  # noqa: BLE001 — 控制流异常由用例断言
        return events, exc
    return events, None


async def _run_step_once(executor: Executor):
    step = PlanStep(id="s1", description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    return await _drive(
        executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
        )
    )


def _cfg(**overrides) -> AgentConfig:
    base = dict(
        enable_planning=False,
        enable_reflection=False,
        enable_memory=False,
        enable_skills=False,
        enable_experience=False,
        enable_thinking=False,
        disabled_tools=[],
    )
    base.update(overrides)
    return AgentConfig(**base)


def test_alias_map_targets_exist_and_risk_matches_canonical():
    """每个别名都指向已注册工具；风险/审批判定与规范名完全一致。"""
    for alias, spec in TOOL_ALIASES.items():
        assert tool_registry.get(spec.target) is not None, f"{alias} -> {spec.target} 未注册"
        assert approval.tool_risk_level(alias) == approval.tool_risk_level(spec.target)
        for mode in ("never", "risky", "always"):
            assert approval.requires_approval(mode, alias) == approval.requires_approval(
                mode, spec.target
            )


def test_alias_to_high_tool_requires_approval(monkeypatch):
    """别名指向 high 工具：risky 模式必须要求审批（不得按别名 low 放行）。"""
    monkeypatch.setitem(TOOL_ALIASES, "zz_high_alias", ToolAlias("execute_python"))

    assert canonical_tool_name("zz_high_alias") == "execute_python"
    assert approval.tool_risk_level("zz_high_alias") == "high"
    assert approval.requires_approval("risky", "zz_high_alias") is True
    assert approval.requires_approval("never", "zz_high_alias") is False


@pytest.mark.asyncio
async def test_executor_approval_request_uses_canonical_name(monkeypatch):
    """executor 收到别名调用 → 审批预检看到的是规范名。"""
    monkeypatch.setitem(TOOL_ALIASES, "zz_high_alias", ToolAlias("execute_python"))
    cfg = _cfg(approval_mode="risky")
    client = _client_with_tool_calls([("zz_high_alias", "{}", "c1")])
    executor = _executor(cfg, client)
    requests: list[approval.ApprovalRequest] = []

    async def _check(request: approval.ApprovalRequest) -> str:
        requests.append(request)
        return "paused"

    executor.approval_check = _check
    events, exc = await _run_step_once(executor)

    assert isinstance(exc, approval.ApprovalRequired)
    assert requests and requests[0].tool_name == "execute_python"
    assert any(e.type == "approval_required" and e.tool_name == "execute_python" for e in events)


@pytest.mark.asyncio
async def test_executor_ledger_and_sse_use_canonical_name(monkeypatch):
    """旧名 calculate → ledger 登记与 SSE 事件都落规范名 utility。"""
    cfg = _cfg()
    client = _client_with_tool_calls([("calculate", '{"expression": "1+1"}', "c1")])
    executor = _executor(cfg, client)
    executor.tool_ledger_task_id = "task-1"
    registered: list[tuple] = []
    dispatched: list[str] = []

    async def _register(task_id, specs):
        registered.append((task_id, list(specs)))

    async def _dispatch(task_id, call_id):
        dispatched.append(call_id)

    monkeypatch.setattr(executor_mod.tool_ledger, "register_calls", _register)
    monkeypatch.setattr(executor_mod.tool_ledger, "mark_dispatched", _dispatch)
    monkeypatch.setattr(executor_mod.tool_ledger, "record_result", AsyncMock())

    with patch.object(
        tool_registry, "execute_detailed", new=AsyncMock(return_value=ToolResult.ok(data="2"))
    ) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    assert spy.await_args.args[0] == "utility"
    assert spy.await_args.args[1] == {"expression": "1+1", "operation": "calculate"}
    assert registered and registered[0][1][0][1] == "utility"
    tool_events = [e for e in events if e.type in ("tool_call", "tool_result")]
    assert tool_events and {e.tool_name for e in tool_events} == {"utility"}


@pytest.mark.asyncio
async def test_execute_detailed_merges_alias_defaults(monkeypatch):
    """别名默认参数注入；显式参数优先；结果元数据落规范名。"""
    captured: dict = {}

    async def _handler(**kwargs):
        captured.update(kwargs)
        return ToolResult.ok(data="ok")

    tool_registry.register(
        name="zz_alias_target",
        description="t",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    )
    monkeypatch.setitem(
        TOOL_ALIASES, "zz_alias_probe", ToolAlias("zz_alias_target", {"mode": "vector"})
    )
    try:
        result = await tool_registry.execute_detailed(
            "zz_alias_probe", {"query": "q", "mode": "hybrid"}
        )
        assert result.success
        assert captured == {"query": "q", "mode": "hybrid"}
        assert result.meta.tool_name == "zz_alias_target"
    finally:
        tool_registry.unregister("zz_alias_target")


def test_alias_defaults_and_explicit_override():
    name, args = canonicalize_tool_call("vector_search", {"query": "q", "top_k": 3})
    assert name == "search_knowledge_base"
    assert args == {"mode": "vector", "query": "q", "top_k": 3}

    name, args = canonicalize_tool_call("fetch_webpage", {"url": "https://x", "action": "search"})
    assert name == "web_search" and args["action"] == "search"


def test_alias_explicit_none_does_not_override_controlled_defaults():
    """旧客户端把可选字段序列化为 null 时，别名受控默认值不得被静默降级。"""
    _, args = canonicalize_tool_call("vector_search", {"query": "q", "mode": None})
    assert args["mode"] == "vector"

    _, args = canonicalize_tool_call("fetch_webpage", {"url": "https://x", "action": None})
    assert args["action"] == "fetch"

    _, args = canonicalize_tool_call("calculate", {"expression": "1+1", "operation": None})
    assert args["operation"] == "calculate"


@pytest.mark.asyncio
async def test_non_dict_arguments_rejected_by_registry():
    """非对象参数不得被静默清空后照常执行（审批参数 = 执行参数）。"""
    result = await tool_registry.execute_detailed("get_current_time", [1, 2])
    assert not result.success
    assert result.error.code == "serialization_error"


@pytest.mark.asyncio
async def test_executor_rejects_non_object_arguments():
    """executor 对合法 JSON 数组参数同样拒绝（serialization_error，不执行）。"""
    cfg = _cfg()
    client = _client_with_tool_calls([("search_knowledge_base", "[1, 2]", "c1")])
    executor = _executor(cfg, client)

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    assert spy.await_count == 0
    assert any(
        e.type == "tool_error" and getattr(e, "error_type", None) == "serialization_error"
        for e in events
    )


@pytest.mark.asyncio
async def test_enabled_predicate_is_enforced_at_execution_layer():
    """门控工具只“不广告”不够：默认配置下幻觉名也不得执行。"""
    cfg = _cfg()  # enable_delegation=False
    client = _client_with_tool_calls([("delegate_subtask", '{"tasks": ["x"]}', "c1")])
    executor = _executor(cfg, client)

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    assert spy.await_count == 0
    assert any(
        e.type == "tool_error" and getattr(e, "error_type", None) == "tool_gated" for e in events
    )

    # 显式开启后同一调用可执行。
    enabled_cfg = _cfg(enable_delegation=True)
    executor = _executor(
        enabled_cfg, _client_with_tool_calls([("delegate_subtask", '{"tasks": ["x"]}', "c1")])
    )
    with patch.object(
        tool_registry, "execute_detailed", new=AsyncMock(return_value=ToolResult.ok(data="ok"))
    ) as spy:
        _, exc = await _run_step_once(executor)
    assert exc is None and spy.await_count == 1


@pytest.mark.asyncio
async def test_old_disabled_name_blocks_canonical_capability_in_executor():
    """disabled=["calculate"] 必须挡住规范 utility(operation=calculate)，其余 operation 仍可执行。"""
    cfg = _cfg(disabled_tools=["calculate"])
    client = _client_with_tool_calls(
        [("utility", '{"operation": "calculate", "expression": "1+1"}', "c1")]
    )
    executor = _executor(cfg, client)
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)
    assert exc is None
    assert spy.await_count == 0
    assert any(
        e.type == "tool_error" and getattr(e, "error_type", None) == "tool_disabled" for e in events
    )

    # 不匹配旧名能力的规范调用不受影响。
    client2 = _client_with_tool_calls(
        [("utility", '{"operation": "analyze_data", "data": "[1]"}', "c2")]
    )
    executor2 = _executor(_cfg(disabled_tools=["calculate"]), client2)
    with patch.object(
        tool_registry, "execute_detailed", new=AsyncMock(return_value=ToolResult.ok(data="ok"))
    ) as spy2:
        _, exc2 = await _run_step_once(executor2)
    assert exc2 is None and spy2.await_count == 1


@pytest.mark.asyncio
async def test_skills_internal_execute_canonicalizes_and_prechecks():
    """skills._consume_tool_output：规范名 + disabled/审批预检（不得绕过执行层）。"""
    from app.agent.tools import skills as skill_tools

    # 1) disabled_tools 命中规范名：直接拒绝。
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        ok, text = await skill_tools._consume_tool_output(
            "calculate",
            {"expression": "1+1"},
            organization_id=1,
            run_context=SimpleNamespace(extra={"disabled_tools": ["utility"]}),
        )
        assert not ok and "tool_disabled" in text
        spy.assert_not_awaited()

    # 1b) 旧名按能力粒度同样挡住技能内规范调用（calculate → utility/operation）。
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        ok, text = await skill_tools._consume_tool_output(
            "calculate",
            {"expression": "1+1"},
            organization_id=1,
            run_context=SimpleNamespace(extra={"disabled_tools": ["calculate"]}),
        )
        assert not ok and "tool_disabled" in text
        spy.assert_not_awaited()

    # 2) high 工具：技能没有审批通道 → fail-closed（never 静默执行）。
    async def _high_handler(**kwargs):
        return ToolResult.ok(data="x")

    tool_registry.register(
        name="zz_skill_high",
        description="t",
        parameters={"type": "object", "properties": {}},
        handler=_high_handler,
        risk_level="high",
    )
    try:
        with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
            ok, text = await skill_tools._consume_tool_output(
                "zz_skill_high", {}, organization_id=1
            )
            assert not ok and "approval_required" in text
            spy.assert_not_awaited()
    finally:
        tool_registry.unregister("zz_skill_high")
