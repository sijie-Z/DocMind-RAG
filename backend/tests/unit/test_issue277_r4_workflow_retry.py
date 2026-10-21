"""#277 R4：workflow 引擎对确定性错误不再空转重试（fail-fast 分类）。

背景（review_or1268 R4）：execute_with_retry 对一切异常重试 max_retries 次 +
1s/2s 指数退避——守卫/配置类确定性错误（ValueError）重试结果不变，白等约 3s、
白打 3 条 warning。

修复（本文件只覆盖 #277 的 R4 一条，其余条目不在范围内）：
- ValueError / NotImplementedError = 确定性错误 → 立即失败，不重试；
- TransientNodeError（新类；APICallNodeExecutor 的网络类失败用它包裹）与
  其它异常 → 维持指数退避重试。

变异判别：
- 去掉 fail-fast 分支 → TestDeterministicFailFast 两条按 calls==3 红；
- APICall 包裹改回 ValueError → test_api_call_network_failure_remains_retryable
  按类型断言（恰 1 次即抛 ValueError，而非重试满 3 次的 TransientNodeError）红。
"""

import pytest

from app.schemas.workflow import WorkflowNode
from app.services.workflow_engine import (
    APICallNodeExecutor,
    NodeExecutor,
    TransientNodeError,
)


def _make_node(
    node_id: str = "n1", node_type: str = "input", data: dict | None = None
) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, data=data or {}, position={"x": 0, "y": 0})


_STATE = {
    "input": {"text": "x"},
    "messages": [],
    "current_node": None,
    "node_outputs": {},
    "context": {},
    "errors": [],
    "memory": {},
    "iteration_count": 0,
    "tool_results": [],
}


class _CountingExecutor(NodeExecutor):
    """execute 计数；前 succeed_after 次抛 fail_exc，其后返回成功（succeed_after=None 恒抛）。"""

    def __init__(self, fail_exc: Exception, succeed_after: int | None = None, max_retries: int = 3):
        super().__init__(_make_node(), {"max_retries": max_retries, "retry_delay": 0.01})
        self.calls = 0
        self.fail_exc = fail_exc
        self.succeed_after = succeed_after

    async def execute(self, state):
        self.calls += 1
        if self.succeed_after is None or self.calls <= self.succeed_after:
            raise self.fail_exc
        return {"messages": []}


class TestDeterministicFailFast:
    @pytest.mark.asyncio
    async def test_value_error_is_not_retried(self):
        ex = _CountingExecutor(ValueError("缺少组织范围（确定性守卫）"))
        with pytest.raises(ValueError, match="确定性守卫"):
            await ex.execute_with_retry(_STATE)
        assert ex.calls == 1, (
            "确定性 ValueError 必须 fail-fast（变异：去掉 fail-fast 分支 → calls==3）"
        )

    @pytest.mark.asyncio
    async def test_not_implemented_is_not_retried(self):
        ex = _CountingExecutor(NotImplementedError("base executor"))
        with pytest.raises(NotImplementedError):
            await ex.execute_with_retry(_STATE)
        assert ex.calls == 1


class TestTransientStillRetried:
    @pytest.mark.asyncio
    async def test_transient_error_retried_then_succeeds(self):
        ex = _CountingExecutor(TransientNodeError("上游超时"), succeed_after=2)
        result = await ex.execute_with_retry(_STATE)
        assert ex.calls == 3
        assert result == {"messages": []}

    @pytest.mark.asyncio
    async def test_generic_exception_still_retried(self):
        """非 ValueError 的普通异常维持旧行为：重试到上限后抛出。"""
        ex = _CountingExecutor(RuntimeError("boom"), max_retries=2)
        with pytest.raises(RuntimeError):
            await ex.execute_with_retry(_STATE)
        assert ex.calls == 2

    @pytest.mark.asyncio
    async def test_api_call_network_failure_remains_retryable(self, monkeypatch):
        """APICall 的网络类失败包裹为 TransientNodeError：重试满 max_retries 次，不被 fail-fast 掐掉。"""
        node = _make_node("api1", "api_call", {"url": "http://api.example.com/x", "method": "GET"})
        ex = APICallNodeExecutor(node, {"max_retries": 3, "retry_delay": 0.01})
        attempts = {"n": 0}

        monkeypatch.setattr(
            "app.services.workflow_engine.APICallNodeExecutor._validate_url",
            staticmethod(lambda url: url),  # 跳过 DNS/内网校验（本用例只测失败分类）
        )

        class _BoomClient:
            def __init__(self, *args, **kwargs):
                attempts["n"] += 1

            async def __aenter__(self):
                raise RuntimeError("connection reset")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr("httpx.AsyncClient", _BoomClient)

        with pytest.raises(TransientNodeError, match="API 调用失败"):
            await ex.execute_with_retry(_STATE)
        assert attempts["n"] == 3, (
            "网络类失败必须被重试（变异：包裹回 ValueError → fail-fast 只 1 次）"
        )
