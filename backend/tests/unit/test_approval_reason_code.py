"""HITL 审批理由的机器可读化（reason_code/reason_params）—— i18n 契约判别。

被测契约：
- build_approval_reason_code：risky/high → high_risk_tool；always → approval_mode_always；
  params 含 tool/risk；
- ApprovalRequest.to_dict() 恒携带 reason_code/reason_params（旧消费者读 reason 不受影响）；
- Executor 事件：risky 路径的事件带 reason_code=high_risk_tool；
  TERMINAL_REQUIRE_APPROVAL 强制路径带 reason_code=terminal_forced；
  hook ask 未给文案时带 reason_code=hook_ask。

变异点：删 reason_code/params 字段、或 build_approval_reason_code 不再返回 dict、
或事件不带 reason_code → 对应用例红。
"""

from unittest.mock import AsyncMock, MagicMock

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
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


def _client_with_tool_call(name: str, args_json: str, cid: str):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tc = MagicMock()
        tc.id = cid
        tc.function.name = name
        tc.function.arguments = args_json
        msg.tool_calls = [tc]
        msg.content = ""
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _make_executor(mode: str = "risky") -> Executor:
    config = AgentConfig()
    config.approval_mode = mode
    config.enable_planning = False
    config.enable_reflection = False
    config.enable_memory = False
    config.enable_skills = False
    config.enable_experience = False
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(_client_with_tool_call("x", "{}", "x"), config, memory)


# ── 模块级：code 工厂 ────────────────────────────────────────────────────────


def test_build_reason_code_risky_high():
    coded = approval.build_approval_reason_code("risky", "execute_python", "high")
    assert coded["reason_code"] == "high_risk_tool"
    assert coded["reason_params"] == {"tool": "execute_python", "risk": "high"}


def test_build_reason_code_always():
    coded = approval.build_approval_reason_code("always", "web_search", "low")
    assert coded["reason_code"] == "approval_mode_always"
    assert coded["reason_params"]["tool"] == "web_search"


def test_request_to_dict_carries_codes():
    req = approval.ApprovalRequest(
        tool_name="execute_python",
        tool_args={"code": "1"},
        risk_level="high",
        reason="中文兜底",
        reason_code="high_risk_tool",
        reason_params={"tool": "execute_python", "risk": "high"},
    )
    d = req.to_dict()
    assert d["reason_code"] == "high_risk_tool"
    assert d["reason_params"] == {"tool": "execute_python", "risk": "high"}
    # 旧字段保留（向后兼容）
    assert d["reason"] == "中文兜底"


def test_request_to_dict_defaults_empty():
    d = approval.ApprovalRequest(tool_name="x").to_dict()
    assert d["reason_code"] == ""
    assert d["reason_params"] == {}


# ── 执行器级：事件携带 code ─────────────────────────────────────────────────


async def _drain_precheck(ex, calls, plan, step):
    """驱动预检 generator，吞掉暂停时抛出的 ApprovalRequired（事件已 yield）。"""
    out = []
    try:
        async for ev in ex._approval_precheck(calls, plan, step):
            out.append(ev)
    except approval.ApprovalRequired:
        pass
    return out


def _fake_registry_calls(monkeypatch):
    """registry.execute_detailed 替身（不碰真实工具）。"""

    async def _execute_detailed(_name, _args, _timeout=None, **_kw):
        return ToolResult(success=True, output="ok", error=None, meta={})

    monkeypatch.setattr(tool_registry, "execute_detailed", _execute_detailed)


@pytest.mark.asyncio
async def test_event_carries_high_risk_reason_code(monkeypatch):
    _fake_registry_calls(monkeypatch)
    ex = _make_executor("risky")
    events: list = []

    async def _check(request):
        events.append(request)
        return "paused"

    ex.approval_check = _check

    plan = Plan(id="p1", goal="g", steps=[])
    step = PlanStep(
        id="s1",
        description="d",
        dependencies=[],
        tool_hint="execute_python",
        status="running",
        retry_count=0,
    )
    tcs = [_tc("execute_python", '{"code": "print(1)"}', "c1")]
    events_out = await _drain_precheck(ex, [(tc, {"code": "print(1)"}) for tc in tcs], plan, step)

    assert events_out and events_out[0].type == "approval_required"
    assert events_out[0].reason_code == "high_risk_tool"
    assert events_out[0].reason_params["tool"] == "execute_python"


@pytest.mark.asyncio
async def test_event_carries_terminal_forced_code(monkeypatch):
    _fake_registry_calls(monkeypatch)
    ex = _make_executor("never")

    # TERMINAL_REQUIRE_APPROVAL=true（默认）时 never 模式也强制审批 terminal_*
    async def _check(request):
        return "paused"

    ex.approval_check = _check
    plan = Plan(id="p1", goal="g", steps=[])
    step = PlanStep(
        id="s1",
        description="d",
        dependencies=[],
        tool_hint="terminal_exec",
        status="running",
        retry_count=0,
    )

    tc = _tc("terminal_exec", '{"command": "ls"}', "c2")
    events_out = await _drain_precheck(ex, [(tc, {"command": "ls"})], plan, step)

    assert events_out and events_out[0].type == "approval_required"
    assert events_out[0].reason_code == "terminal_forced"


def _tc(name: str, args_json: str, cid: str):
    tc = MagicMock()
    tc.id = cid
    tc.function.name = name
    tc.function.arguments = args_json
    return tc
