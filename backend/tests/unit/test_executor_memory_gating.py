"""T5：执行器记忆回忆受 enable_memory 门控（浪费 + 延迟 + 端点异常退避放大）。

先红：修复前 `_execute_step_once` 无条件 `await memory.get_context_for_query(...)`
——enable_memory=False 的子代理/会话仍每步打 embedding 端点。
修复后：关闭记忆 → 零回忆调用且回忆文本不进入提示词；开启记忆 → 照常回忆并进入提示词。

变异点：删除 executor 中的 enable_memory 门 → 关闭态用例红（await_count 变 1）。
显式引用/@文档（loop 的引用路径）不属于被动回忆，不受此门控；本用例只钉执行器路径。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.planner import Plan, PlanStep


def _recording_failing_client(sink: dict) -> MagicMock:
    """把一次 LLM 调用的 messages 录进 sink，再以异常快速失败（免真网络）。"""
    client = MagicMock()

    async def _fail(*, messages, tools=None, **kwargs):
        sink["messages"] = messages
        raise ConnectionError("provider down")

    client.chat.completions.create = _fail
    return client


async def _run_once(ex: Executor, step: PlanStep, plan: Plan):
    return [
        e
        async for e in ex._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
        )
    ]


def _config(enable_memory: bool) -> AgentConfig:
    return AgentConfig(
        timeout=0.5,
        enable_planning=False,
        enable_reflection=False,
        enable_memory=enable_memory,
    )


def _messages_text(sink: dict) -> str:
    return "\n".join(str(m.get("content", "")) for m in sink.get("messages", []))


@pytest.mark.asyncio
async def test_memory_disabled_skips_recall_entirely():
    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="MEMORY-TEXT-SHOULD-NOT-APPEAR")
    sink: dict = {}
    ex = Executor(_recording_failing_client(sink), _config(enable_memory=False), memory)
    step = PlanStep(id="s1", description="查一下天气")
    plan = Plan(id="p1", goal="g", steps=[step])

    await _run_once(ex, step, plan)

    assert memory.get_context_for_query.await_count == 0, (
        "enable_memory=False 时执行器不得调用记忆回忆（变异：删门控 → 本断言红）"
    )
    assert "MEMORY-TEXT-SHOULD-NOT-APPEAR" not in _messages_text(sink), (
        "关闭记忆时回忆文本不得混入提示词（门控要断在调用处，而不是只断指标）"
    )


@pytest.mark.asyncio
async def test_memory_enabled_calls_recall_once():
    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="历史经验：先查文档。")
    sink: dict = {}
    ex = Executor(_recording_failing_client(sink), _config(enable_memory=True), memory)
    step = PlanStep(id="s1", description="查一下天气")
    plan = Plan(id="p1", goal="g", steps=[step])

    await _run_once(ex, step, plan)

    assert memory.get_context_for_query.await_count == 1, "开启记忆时回忆必须照常调用（误伤对照）"
    assert "历史经验：先查文档。" in _messages_text(sink), (
        "开启记忆时回忆文本必须真正进入提示词构建（任务书口径）"
    )
