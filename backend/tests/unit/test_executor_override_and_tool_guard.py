"""#199 评审三个 blocker 的闭合验收（B1 / B2 / B3）。

- **B1**：`config.system_prompt_override` 必须到达 executor 组装的系统消息
  （此前 override 只在 `loop._build_messages` 被消费，executor 路径完全无视它——
  子代理的身份指令与父上下文永远到不了子代理 LLM）。
- **B2**：executor 的工具调用超时必须取 registry 里声明的值（此前写死 30s，
  把声明 600s 的委派在生产路径上掐死）。
- **B3**：受限上下文（`tool_tags` 非空）必须**在执行层**拒绝未列名工具
  （此前仅 schema 层过滤：模型凭空发出未列名工具照样执行并逐级递归）。

驱动方式：假 LLM 的 `chat.completions.create` + 直接驱动 `Executor._execute_step_once`。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolMeta, ToolResult, tool_registry


def _client_with_tool_call(
    tool_name: str, raw_arguments: str | None = "{}", capture: dict | None = None
):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        if capture is not None:
            capture.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "tc-x"
        tc.function.name = tool_name
        tc.function.arguments = raw_arguments
        msg.tool_calls = [tc]
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _mk_executor(config: AgentConfig, client) -> Executor:
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(client, config, memory)


async def _run(executor):
    step = PlanStep(id="s1", description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    events = []
    async for ev in executor._execute_step_once(
        step=step,
        plan=plan,
        history=None,
        organization_id=2,
        user_id=0,
        enable_thinking=False,
    ):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_b1_override_reaches_executor_system_prompt():
    capture: dict = {}

    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        capture.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = "done"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create

    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        system_prompt_override="CTX_MARKER_42 你是子任务代理",
    )
    await _run(_mk_executor(cfg, client))

    sys_msgs = [m for m in capture["messages"][0] if m.get("role") == "system"]
    assert sys_msgs, "必须有系统消息"
    assert "CTX_MARKER_42" in sys_msgs[0]["content"], (
        "B1：system_prompt_override 必须到达 executor 的系统消息"
    )


@pytest.mark.asyncio
async def test_b3_disallowed_tool_rejected_at_execution_layer():
    client = _client_with_tool_call("delegate_subtask")  # 不在 search/analysis 白名单内
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_tags=["search", "analysis"],
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events = await _run(_mk_executor(cfg, client))

    assert spy.await_count == 0, "B3：未列名工具必须在执行层被拒（不得触达 registry）"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs, "应发出显式 tool_error"
    assert errs[0].error_type == "tool_not_allowed"


@pytest.mark.asyncio
async def test_b3_allowed_tool_still_executes():
    client = _client_with_tool_call("search_knowledge_base")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_tags=["search", "analysis"],
        max_tool_calls_per_turn=3,
    )

    class _Ok:
        success = True
        error = None

        def __str__(self):
            return "ok"

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())) as spy:
        events = await _run(_mk_executor(cfg, client))

    assert spy.await_count == 1, "白名单内工具应正常执行"
    assert any(e.type == "tool_result" for e in events)


def test_b2_tool_timeout_resolved_from_registry():
    ex = _mk_executor(AgentConfig(), MagicMock())
    assert ex._tool_timeout("delegate_subtask") == 601.0, (
        "B2：声明 600s 的工具不得被 30s 掐死（外层=声明值+1s 缓冲）"
    )
    assert ex._tool_timeout("calculate") == ex.TOOL_TIMEOUT_SECONDS + 1.0
    assert ex._tool_timeout("不存在的工具") == ex.TOOL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_synthesis_also_honors_override():
    """评审 minor：合成阶段（子代理的最终答复）同样带 override 人设。"""
    captured: dict = {}

    client = _client_with_tool_call("search_knowledge_base", capture=captured)
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_tags=["search", "analysis"],
        max_tool_calls_per_turn=1,
        system_prompt_override="CTX_MARKER_42 你是子任务代理",
    )

    class _Ok:
        success = True
        error = None

        def __str__(self):
            return "ok"

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())):
        await _run(_mk_executor(cfg, client))

    assert len(captured["messages"]) >= 2, "应有工具循环 + 合成两次 LLM 调用"
    synthesis_sys = [m for m in captured["messages"][-1] if m.get("role") == "system"]
    assert synthesis_sys and "CTX_MARKER_42" in synthesis_sys[0]["content"], (
        "合成阶段的系统消息也必须带 override 人设"
    )


@pytest.mark.asyncio
async def test_unrestricted_context_executes_normally():
    """主代理（tool_tags=None）不设白名单：显式启用 delegate_subtask 后可触达 registry。"""
    client = _client_with_tool_call("delegate_subtask")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        max_tool_calls_per_turn=3,
        # issue #509：委派默认关闭（注入+执行门控），本用例验证白名单不误拦，
        # 因此显式开启。
        enable_delegation=True,
    )

    class _Ok:
        success = True
        error = None

        def __str__(self):
            return "ok"

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())) as spy:
        await _run(_mk_executor(cfg, client))

    assert spy.await_count == 1, "无限制上下文不应拦截任何工具"


@pytest.mark.asyncio
async def test_synthesis_uses_structural_truncation():
    """issue #203 ④：工具结果进合成 LLM 前走 `_smart_truncate`（结构化截断）。

    变异点：把 `_smart_truncate(r['result'], r['tool'])` 改回 `r['result'][:1500]` →
    本测试必红（无 "... and N more items" 标记，且 doc-14 行放不进 1500 字符）。
    """
    captured: dict = {}
    client = _client_with_tool_call("list_documents", capture=captured)
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        max_tool_calls_per_turn=1,
    )

    # 60 行 × ~140 字符 ≈ 8.4k 字符 > 6000 阈值 → 触发 list 分支（保前 15 行 + 计数标记）
    payload = "\n".join(
        f"- ID: doc-{i:02d} | 文件名: very-long-filename-{i}-{'q' * 60}.pdf"
        f" | 状态: indexed | 创建: 2026-01-01"
        for i in range(60)
    )

    class _Long:
        success = True
        error = None

        def __str__(self):
            return payload

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Long())):
        await _run(_mk_executor(cfg, client))

    synthesis_msgs = captured["messages"][-1]
    user_msgs = [m for m in synthesis_msgs if m.get("role") == "user"]
    assert user_msgs, "合成调用应有 user 消息"
    synth_user = user_msgs[0]["content"]
    assert "- ID: doc-14" in synth_user and "doc-15" not in synth_user, (
        "list 分支应保留前 15 行、丢弃第 16 行起的条目"
    )
    assert "... and 45 more items" in synth_user, "应带计数标记（裸切不会产生）"


# ── issue #206：执行层禁用清单 + sandbox 元数据随 SSE 暴露 ────────────────────


@pytest.mark.asyncio
async def test_config_disabled_tool_rejected_at_dispatch_layer():
    """#206（可选加固）：服务端强制合并的 disabled_tools 必须在**执行层**生效。

    工具 schema 过滤（_get_step_tools）只影响呈现给 LLM 的列表；模型凭空发出的
    禁用工具此前仍会被执行。变异点：删掉派发前的 disabled_tools 判定 → 本用例红
    （execute_detailed 被调用、无 tool_disabled 事件）。
    """
    client = _client_with_tool_call("execute_python", '{"code": "print(1)"}')
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        disabled_tools=["execute_python"],
        max_tool_calls_per_turn=3,
    )

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events = await _run(_mk_executor(cfg, client))

    assert spy.await_count == 0, "禁用工具绝不能触达 registry"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs, "应发出显式 tool_error"
    assert errs[0].error_type == "tool_disabled"
    assert "execute_python" in errs[0].content


@pytest.mark.asyncio
async def test_tool_result_event_carries_sandbox_meta():
    """#206：resolved sandbox（ToolMeta.sandbox）必须随 SSE tool_result 暴露。

    变异点：executor 不透传 meta.sandbox → 事件与 SSE 载荷都没有 sandbox 字段 → 红。
    """
    client = _client_with_tool_call("execute_python", '{"code": "print(1)"}')
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        disabled_tools=[],  # 本用例聚焦元数据透传，显式启用该工具
        approval_mode="never",  # 不测试审批；避免高风险工具的无通道门掩盖 sandbox 断言
        max_tool_calls_per_turn=3,
    )
    spy = AsyncMock(return_value=ToolResult.ok(data="45", meta=ToolMeta(sandbox="ast")))

    with patch.object(tool_registry, "execute_detailed", new=spy):
        events = await _run(_mk_executor(cfg, client))

    results = [e for e in events if e.type == "tool_result"]
    assert results, "应发出 tool_result"
    assert results[0].sandbox == "ast"
    assert results[0].to_sse_dict()["sandbox"] == "ast"
