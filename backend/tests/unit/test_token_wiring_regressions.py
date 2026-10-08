"""#239 minor-7：两处"用量接线"的回归钉子（#202 遗留）。

planner 流式采集与 loop 无工具直答分支此前无自动化测试（评审实测可用但无保护）。
两处各一条判别性用例：删掉对应 `record_llm_usage(...)` 调用，用例必须变红。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Planner


def _chunk(content=None, usage=None):
    c = MagicMock()
    if content is None:
        c.choices = []
    else:
        c.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]
    c.usage = usage
    return c


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.asyncio
async def test_planner_streaming_collects_usage_into_ctx():
    """变异点：删 planner 里 `record_llm_usage(SimpleNamespace(usage=stream_usage), ctx)`
    → 本用例红（ctx.total_tokens 变回 0）。"""
    plan_json = (
        '{"goal": "g", "reasoning": "r", '
        '"steps": [{"id": "s1", "description": "d", "dependencies": []}]}'
    )
    chunks = [
        _chunk("```json\n"),
        _chunk(plan_json),
        _chunk("\n```"),
        _chunk(None, SimpleNamespace(prompt_tokens=100, completion_tokens=20)),
    ]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))

    planner = Planner(client, AgentConfig())
    ctx = ExecutionContext(query="q")

    [_ async for _ in planner.plan("q", ctx=ctx)]

    assert ctx.total_tokens == 120, (
        "流式 usage（include_usage 末块）必须经单一入口入账（#202 / #239 minor-7）"
    )
    sent = client.chat.completions.create.await_args.kwargs
    assert sent.get("stream_options") == {"include_usage": True}, (
        "请求侧必须显式索取 usage（评审 minor-1：删掉该参数此前无断言拦截）"
    )


@pytest.mark.asyncio
async def test_toolless_run_accounts_usage_into_execution_context(monkeypatch):
    """变异点：删 loop 无工具直答分支的 `record_llm_usage(response, ctx)` → 本用例红。"""
    from app.agent.loop import PERAgentLoop

    async def _no_save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _no_save)

    class _FakeMemory:
        def reset_working(self):
            pass

        def get_all_results(self):
            return {}

        def store_step_result(self, *a, **k):
            pass

        def get_step_result(self, *a, **k):
            return None

        async def record_interaction(self, *a, **k):
            pass

    resp = MagicMock()
    msg = MagicMock()
    msg.content = "直答内容"
    msg.tool_calls = None
    resp.choices = [MagicMock()]
    resp.choices[0].message = msg
    resp.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    loop = PERAgentLoop(
        client,
        AgentConfig(
            enable_planning=False, enable_tools=False, enable_reflection=False,
            enable_memory=False, enable_thinking=False, enable_experience=False,
        ),
        organization_id=1, user_id=0,
    )
    loop.memory = _FakeMemory()
    loop._write_experiences = AsyncMock()

    events = await asyncio.wait_for(_collect(loop), timeout=15.0)

    ctx_events = [e for e in events if e.type == "execution_context"]
    assert ctx_events, "run 必须发出 execution_context 事件"
    assert ctx_events[-1].content["total_tokens"] == 60, (
        "无工具直答分支的 usage 必须入账（#202 / #239 minor-7）"
    )
    assert any(e.type == "done" for e in events)


async def _collect(loop) -> list:
    return [e async for e in loop.run("问题")]
