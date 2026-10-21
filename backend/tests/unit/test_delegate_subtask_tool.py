"""issue #199 接线验收：`delegate_subtask` 工具（subagent 从孤岛到接入主循环）。

钉住验收面：工具可见性 / 结果回传（顺序保持）/ 子代理受限工具集与**递归防护** /
并发上限 / 单任务超时 / 错误传播（单任务异常不外溢、整体配置错误走 ToolResult.fail）。

驱动方式：假 LLM（MagicMock 的 `chat.completions.create`）——子代理 PERAgentLoop
在工具循环里拿到的就是它；不触达任何外部 API。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.agent.core_tools  # noqa: F401 — 注册带 search/analysis tag 的工具
import app.agent.tools  # noqa: F401 — 注册含 delegate_subtask 的全部工具
from app.agent.registry import tool_registry


def _fake_llm(captured_tool_lists: list | None = None):
    """假 AsyncOpenAI：每次 create 返回「无 tool_calls 的最终答案」，回显任务词。"""
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        if captured_tool_lists is not None:
            captured_tool_lists.append([t["function"]["name"] for t in (tools or [])])
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break
        msg.content = f"结论<<{last_user[:24]}>>"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _stub_pipeline(client):
    return SimpleNamespace(openai_client=client)


@pytest.mark.asyncio
async def test_tool_visible_and_recursion_guarded_in_child():
    captured: list = []
    fake = _fake_llm(captured)
    with patch("app.dependencies.get_rag_pipeline", return_value=_stub_pipeline(fake)):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["查清甲材料", "查清乙材料"], "context": "背景X"},
            organization_id=2,
            user_id=0,
        )

    assert result.success, f"工具执行失败: {result.error}"
    data = result.data
    assert "查清甲材料" in data and "查清乙材料" in data, "结果回传应含两个子任务"
    assert data.index("查清甲材料") < data.index("查清乙材料"), "结果应保持任务顺序"

    assert captured, "子代理必须真的调用了 LLM"
    child_tools = set(captured[0])
    assert "delegate_subtask" not in child_tools, "递归防护：子代理不得看到委派工具"
    assert "search_knowledge_base" in child_tools, "子代理应持有受限的检索类工具"


def test_tool_registered_with_delegation_tag_only_for_parent():
    names_all = {t["function"]["name"] for t in tool_registry.to_openai_tools()}
    assert "delegate_subtask" in names_all
    names_child = {
        t["function"]["name"] for t in tool_registry.to_openai_tools(["search", "analysis"])
    }
    assert "delegate_subtask" not in names_child, "递归防护的静态面：tag 过滤"


@pytest.mark.asyncio
async def test_more_than_three_tasks_rejected():
    result = await tool_registry.execute_detailed(
        "delegate_subtask",
        {"tasks": ["1", "2", "3", "4"]},
        organization_id=1,
        user_id=0,
    )
    assert not result.success
    assert result.error.code == "validation_error"


@pytest.mark.asyncio
async def test_no_client_returns_explicit_failure():
    with patch("app.dependencies.get_rag_pipeline", return_value=_stub_pipeline(None)):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["x"]},
            organization_id=1,
            user_id=0,
        )
    assert not result.success
    assert result.error.code == "invalid_configuration"


@pytest.mark.asyncio
async def test_concurrency_capped_at_three_and_order_kept(monkeypatch):
    import app.agent.subagent as sa

    inflight = 0
    peak = 0

    async def fake_delegate(client, task, parent_context="", model="m", organization_id=1):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)
        yield SimpleNamespace(type="chunk", content=task)
        inflight -= 1

    monkeypatch.setattr(sa, "delegate_task", fake_delegate)
    results = await sa.run_parallel_subtasks(
        client=None, tasks=[f"t{i}" for i in range(6)], organization_id=1, per_task_timeout=None
    )
    assert peak <= 3, f"并发上限应为 3，实测峰值 {peak}"
    assert results == [f"t{i}" for i in range(6)]


@pytest.mark.asyncio
async def test_per_task_timeout_marks_without_killing_batch(monkeypatch):
    import app.agent.subagent as sa

    async def slow_delegate(client, task, parent_context="", model="m", organization_id=1):
        if task == "slow":
            await asyncio.sleep(0.5)
        yield SimpleNamespace(type="chunk", content=task)

    monkeypatch.setattr(sa, "delegate_task", slow_delegate)
    results = await sa.run_parallel_subtasks(
        client=None, tasks=["slow", "fast"], organization_id=1, per_task_timeout=0.05
    )
    assert results[0].startswith("[子任务超时"), f"超时子任务应带标注: {results[0]}"
    assert results[1] == "fast", "其余子任务不受拖累"


@pytest.mark.asyncio
async def test_single_task_error_contained(monkeypatch):
    import app.agent.subagent as sa

    async def flaky_delegate(client, task, parent_context="", model="m", organization_id=1):
        if task == "boom":
            raise RuntimeError("炸了")
        yield SimpleNamespace(type="chunk", content="ok:" + task)

    monkeypatch.setattr(sa, "delegate_task", flaky_delegate)
    results = await sa.run_parallel_subtasks(
        client=None, tasks=["boom", "fine"], organization_id=1, per_task_timeout=None
    )
    assert results[0].startswith("[Error: RuntimeError"), results[0]
    assert results[1] == "ok:fine"
