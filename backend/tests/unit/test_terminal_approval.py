"""issue #509：terminal 强制审批按 tag/category 判定 + 伞形名不得漏网。

覆盖：
- ``approval.is_terminal_tool`` 按注册表 category/tag 判定，不依赖名字前缀；
- 未注册的伞形名 ``terminal`` 仍命中（fail-closed 回退）；
- ``terminal_open`` / ``terminal`` + TERMINAL_REQUIRE_APPROVAL=true +
  approval_mode=never + 无审批通道 → deny（不执行）；
- 显式关闭 TERMINAL_REQUIRE_APPROVAL 后才放行。
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
from app.core.config import settings


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
    except BaseException as exc:  # noqa: BLE001
        return events, exc
    return events, None


async def _run_step_once(executor: Executor):
    step = PlanStep(id="s1", description="执行终端操作")
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
        approval_mode="never",
        disabled_tools=[],
    )
    base.update(overrides)
    return AgentConfig(**base)


def test_terminal_detection_uses_category_and_tags_not_prefix():
    """注册条目按 category/tag 判定；前缀不再是唯一依据。"""
    assert approval.is_terminal_tool("terminal_open") is True
    # 未注册的伞形名仍 fail-closed 命中（回退判定）。
    assert approval.is_terminal_tool("terminal") is True

    async def _handler(**kwargs):
        return ToolResult.ok(data="x")

    tool_registry.register(
        name="terminal_evil",
        description="prefix but not terminal category",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        category="code",
        tags=["code"],
    )
    tool_registry.register(
        name="my_umbrella",
        description="terminal category without prefix",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        category="terminal",
        tags=["shell"],
    )
    try:
        assert approval.is_terminal_tool("terminal_evil") is False, "不得仅凭前缀判定"
        assert approval.is_terminal_tool("my_umbrella") is True
    finally:
        tool_registry.unregister("terminal_evil")
        tool_registry.unregister("my_umbrella")


def test_terminal_forced_follows_require_approval_switch(monkeypatch):
    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    assert Executor._terminal_approval_forced("terminal_open") is True
    assert Executor._terminal_approval_forced("terminal") is True
    assert Executor._terminal_approval_forced("search_knowledge_base") is False

    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", False)
    assert Executor._terminal_approval_forced("terminal_open") is False


@pytest.mark.asyncio
async def test_terminal_without_approval_channel_is_denied(monkeypatch):
    """terminal + 强制审批 + never + 无通道 → deny，且绝不执行。"""
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    client = _client_with_tool_calls([("terminal_open", "{}", "c1")])
    executor = _executor(_cfg(approval_mode="never"), client)
    executor.approval_check = None

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    errors = [e for e in events if e.type == "tool_error"]
    assert errors and errors[0].error_type == "approval_denied"
    assert errors[0].tool_name == "terminal_open"
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_umbrella_name_is_denied_without_channel(monkeypatch):
    """伞形名 ``terminal``（未注册）同样强制审批：不得因名字不匹配而放行。"""
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    client = _client_with_tool_calls([("terminal", "{}", "c1")])
    executor = _executor(_cfg(approval_mode="never"), client)
    executor.approval_check = None

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    errors = [e for e in events if e.type == "tool_error"]
    assert errors and errors[0].error_type == "approval_denied"
    assert errors[0].tool_name == "terminal"
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_disabled_is_gated_before_approval(monkeypatch):
    """TERMINAL_ENABLED=false（默认）→ 执行层直接 tool_gated，不进入审批。"""
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", False)
    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    client = _client_with_tool_calls([("terminal_open", "{}", "c1")])
    executor = _executor(_cfg(approval_mode="never"), client)
    executor.approval_check = None

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    errors = [e for e in events if e.type == "tool_error"]
    assert errors and errors[0].error_type == "tool_gated"
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_allowed_when_require_approval_disabled(monkeypatch):
    """显式关闭 TERMINAL_REQUIRE_APPROVAL 后，never 模式且无通道时可执行。"""
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", False)
    client = _client_with_tool_calls([("terminal_open", "{}", "c1")])
    executor = _executor(_cfg(approval_mode="never"), client)
    executor.approval_check = None

    with patch.object(
        tool_registry, "execute_detailed", new=AsyncMock(return_value=ToolResult.ok(data="ok"))
    ) as spy:
        events, exc = await _run_step_once(executor)

    assert exc is None
    assert spy.await_count == 1
    assert not any(
        e.type == "tool_error" and getattr(e, "error_type", None) == "approval_denied"
        for e in events
    )
