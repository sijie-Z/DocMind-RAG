"""竞品对标 P1：工作流工具白名单与单次运行预算（fail-closed）。"""

import pytest
from langchain_core.messages import AIMessage

from app.schemas.workflow import WorkflowConfig, WorkflowEdge, WorkflowNode
from app.services.workflow_engine import (
    WorkflowBudgetExceededError,
    WorkflowEngine,
    WorkflowToolNotAllowedError,
)


class _FakeSearchExecutor:
    """记录调用次数，证明被拒绝的工具根本没被调用。"""

    calls = 0

    async def execute_with_retry(self, state):
        type(self).calls += 1
        return {"context": {"knowledge_context": "should-not-happen"}}


class _StubLLM:
    def __init__(self, response: AIMessage):
        self.response = response

    async def ainvoke(self, messages):
        return self.response


def _patch_tool_executor(monkeypatch):
    """只替换被观测的工具节点，input/output 仍用真实执行器。"""
    real_get_executor = WorkflowEngine.get_executor

    def get_executor(self, node):
        if node.type == "tool_search":
            return _FakeSearchExecutor()
        return real_get_executor(self, node)

    monkeypatch.setattr(WorkflowEngine, "get_executor", get_executor)
    _FakeSearchExecutor.calls = 0


def _node(node_id: str, node_type: str) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, data={}, position={"x": 0, "y": 0})


def _flow(*node_types: str, **limits) -> WorkflowConfig:
    nodes = [_node(f"n{i}", t) for i, t in enumerate(node_types)]
    edges = [
        WorkflowEdge(id=f"e{i}", source=nodes[i].id, target=nodes[i + 1].id)
        for i in range(len(nodes) - 1)
    ]
    return WorkflowConfig(nodes=nodes, edges=edges, **limits)


def _llm_response(tokens_in: int, tokens_out: int) -> AIMessage:
    return AIMessage(
        content="ok",
        response_metadata={"model_name": "gpt-4o-mini"},
        usage_metadata={
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )


class TestWorkflowToolAllowlist:
    @pytest.mark.asyncio
    async def test_without_allowlist_tool_is_called(self, monkeypatch):
        """缺省 must be fully backward compatible: no allowlist -> no restriction."""
        _patch_tool_executor(monkeypatch)
        engine = WorkflowEngine(_flow("input", "tool_search", "output"))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is True
        assert _FakeSearchExecutor.calls == 1

    @pytest.mark.asyncio
    async def test_unlisted_tool_is_rejected_without_being_called(self, monkeypatch):
        _patch_tool_executor(monkeypatch)
        engine = WorkflowEngine(
            _flow("input", "tool_search", "output", allowed_tools=["llm_openai"])
        )

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert "tool_search" in result["error"]
        assert "allowed_tools" in result["error"]
        assert _FakeSearchExecutor.calls == 0  # 判别性：工具没被调用

    @pytest.mark.asyncio
    async def test_empty_allowlist_rejects_all_tools(self, monkeypatch):
        _patch_tool_executor(monkeypatch)
        engine = WorkflowEngine(_flow("input", "tool_search", "output", allowed_tools=[]))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert _FakeSearchExecutor.calls == 0

    @pytest.mark.asyncio
    async def test_listed_tool_still_runs(self, monkeypatch):
        _patch_tool_executor(monkeypatch)
        engine = WorkflowEngine(
            _flow("input", "tool_search", "output", allowed_tools=["tool_search"])
        )

        result = await engine.execute({"text": "hi"})

        assert result["success"] is True
        assert _FakeSearchExecutor.calls == 1

    def test_allowlist_config_normalizes_duplicates_and_blank_items(self):
        config = WorkflowConfig(
            nodes=[], edges=[], allowed_tools=["tool_search", " tool_search ", ""]
        )
        assert config.allowed_tools == ["tool_search"]

    def test_absent_budget_fields_default_to_none(self):
        config = WorkflowConfig(nodes=[], edges=[])
        assert (config.allowed_tools, config.max_tokens, config.max_cost_usd) == (None, None, None)


class _RecordingLLM:
    """按预设响应序列返回，记录真实调用次数。"""

    def __init__(self, *responses: AIMessage):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class TestWorkflowRunBudget:
    @pytest.mark.asyncio
    async def test_no_budget_keeps_legacy_behavior(self, monkeypatch):
        llm = _RecordingLLM(_llm_response(100, 50))
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(_flow("input", "llm_openai", "output"))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is True
        assert llm.calls == 1

    @pytest.mark.asyncio
    async def test_exact_budget_allows_single_llm_call(self, monkeypatch):
        """恰好等于上限不算超支（off-by-one 判别）。"""
        llm = _RecordingLLM(_llm_response(100, 50))
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(_flow("input", "llm_openai", "output", max_tokens=150))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is True
        assert llm.calls == 1

    @pytest.mark.asyncio
    async def test_max_tokens_exceeded_fails_run(self, monkeypatch):
        llm = _RecordingLLM(_llm_response(100, 51))
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(_flow("input", "llm_openai", "output", max_tokens=150))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert "token 预算" in result["error"]
        assert "151" in result["error"] and "150" in result["error"]
        assert llm.calls == 1

    @pytest.mark.asyncio
    async def test_max_cost_usd_exceeded_fails_run(self, monkeypatch):
        llm = _RecordingLLM(_llm_response(1000, 1000))
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(_flow("input", "llm_openai", "output", max_cost_usd=0.0001))

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert "成本预算" in result["error"]
        assert "0.000100" in result["error"]

    @pytest.mark.asyncio
    async def test_budget_stops_following_llm_node(self, monkeypatch):
        """耗超预算后，下游付费节点不得再被调用。"""
        llm = _RecordingLLM(_llm_response(100, 100))
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(
            _flow("input", "llm_openai", "llm_openai", "output", max_tokens=150)
        )

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert "token 预算" in result["error"]
        assert llm.calls == 1  # 判别性：第二个 LLM 没被调用

    @pytest.mark.asyncio
    async def test_missing_usage_fail_closed_when_budget_requested(self, monkeypatch):
        """provider 不给 usage 时拒绝继续付费调用，绝不静默当作 0。"""
        no_usage = AIMessage(content="ok")
        llm = _RecordingLLM(no_usage)
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm", lambda self, _t="openai": llm
        )
        engine = WorkflowEngine(
            _flow("input", "llm_openai", "llm_openai", "output", max_tokens=150)
        )

        result = await engine.execute({"text": "hi"})

        assert result["success"] is False
        assert "预算无法核验" in result["error"]
        assert llm.calls == 1

    def test_guard_exceptions_are_value_errors(self):
        assert issubclass(WorkflowToolNotAllowedError, ValueError)
        assert issubclass(WorkflowBudgetExceededError, ValueError)


class TestBudgetSchemaValidation:
    def test_negative_budget_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowConfig(nodes=[], edges=[], max_tokens=-1)
        with pytest.raises(ValidationError):
            WorkflowConfig(nodes=[], edges=[], max_cost_usd=-0.1)

    @pytest.mark.asyncio
    async def test_config_values_flow_into_engine(self):
        config = WorkflowConfig(
            nodes=[], edges=[], allowed_tools=["tool_search"], max_tokens=10, max_cost_usd=0.5
        )
        engine = WorkflowEngine(config)
        assert engine.allowed_tools == ["tool_search"]
        assert engine.max_tokens == 10
        assert engine.max_cost_usd == 0.5
        # 不需要外部依赖即可单测守卫
        with pytest.raises(WorkflowToolNotAllowedError, match="tool_tts"):
            engine._check_tool_allowed(_node("x", "tool_tts"))
        engine._check_tool_allowed(_node("y", "tool_search"))
