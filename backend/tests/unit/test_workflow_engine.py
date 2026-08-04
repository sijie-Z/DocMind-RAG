"""Workflow engine unit tests — node executors, conditions, transforms, routing."""
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import settings
from app.schemas.workflow import WorkflowConfig, WorkflowNode
from app.services.workflow_engine import (
    CodeExecuteNodeExecutor,
    ConditionNodeExecutor,
    ExecutionStatus,
    InputNodeExecutor,
    OutputNodeExecutor,
    RouterNodeExecutor,
    TransformNodeExecutor,
    TTSNodeExecutor,
    WorkflowEngine,
    WorkflowState,
    merge_dicts,
    merge_lists,
)

# ── Helpers ──────────────────────────────────────────────────────────

def _make_node(node_id: str = "n1", node_type: str = "input", data: dict = None) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, data=data or {}, position={"x": 0, "y": 0})


def _make_state(**overrides) -> WorkflowState:
    base: WorkflowState = {
        "input": {"text": "hello"},
        "messages": [],
        "current_node": None,
        "node_outputs": {},
        "context": {},
        "errors": [],
        "memory": {},
        "iteration_count": 0,
        "tool_results": [],
    }
    base.update(overrides)
    return base


# ── Pure functions ───────────────────────────────────────────────────

class TestMergeFunctions:
    def test_merge_dicts_prefers_right(self):
        assert merge_dicts({"a": 1}, {"a": 2, "b": 3}) == {"a": 2, "b": 3}

    def test_merge_dicts_empty(self):
        assert merge_dicts({}, {}) == {}

    def test_merge_lists_concatenates(self):
        assert merge_lists([1, 2], [3, 4]) == [1, 2, 3, 4]

    def test_merge_lists_empty(self):
        assert merge_lists([], []) == []


class TestExecutionStatus:
    def test_values_are_strings(self):
        assert ExecutionStatus.PENDING == "pending"
        assert ExecutionStatus.RUNNING == "running"
        assert ExecutionStatus.COMPLETED == "completed"
        assert ExecutionStatus.FAILED == "failed"


# ── InputNodeExecutor ────────────────────────────────────────────────

class TestInputNodeExecutor:
    @pytest.mark.asyncio
    async def test_template_replacement(self):
        node = _make_node(data={"prompt": "请翻译：{{input}}"})
        state = _make_state(input={"text": "hello world"})
        result = await InputNodeExecutor(node).execute(state)
        assert "hello world" in result["messages"][0].content
        assert result["context"]["original_input"] == "hello world"

    @pytest.mark.asyncio
    async def test_no_template_appends_input(self):
        node = _make_node(data={"prompt": "翻译"})
        state = _make_state(input={"text": "hi"})
        result = await InputNodeExecutor(node).execute(state)
        assert "hi" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_empty_prompt_uses_input(self):
        node = _make_node(data={"prompt": ""})
        state = _make_state(input={"text": "just this"})
        result = await InputNodeExecutor(node).execute(state)
        assert result["messages"][0].content == "just this"


# ── OutputNodeExecutor ──────────────────────────────────────────────

class TestOutputNodeExecutor:
    @pytest.mark.asyncio
    async def test_prefers_final_output_from_context(self):
        node = _make_node(data={}, node_type="output")
        state = _make_state(context={"final_output": "context answer"})
        result = await OutputNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["output"] == "context answer"

    @pytest.mark.asyncio
    async def test_falls_back_to_ai_message(self):
        node = _make_node(data={}, node_type="output")
        state = _make_state(messages=[AIMessage(content="ai reply")])
        result = await OutputNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["output"] == "ai reply"

    @pytest.mark.asyncio
    async def test_empty_when_nothing(self):
        node = _make_node(data={}, node_type="output")
        state = _make_state(messages=[])
        result = await OutputNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["output"] == ""


# ── ConditionNodeExecutor ────────────────────────────────────────────

class TestConditionNodeExecutor:
    @pytest.mark.asyncio
    async def test_translate_keyword(self):
        node = _make_node(data={"condition": "translate"})
        state = _make_state(input={"text": "请翻译这段话"})
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "translate"

    @pytest.mark.asyncio
    async def test_summarize_keyword(self):
        node = _make_node(data={"condition": "summarize"})
        state = _make_state(input={"text": "帮我总结一下"})
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "summarize"

    @pytest.mark.asyncio
    async def test_code_keyword(self):
        node = _make_node(data={"condition": "code"})
        state = _make_state(input={"text": "写一段代码"})
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "code"

    @pytest.mark.asyncio
    async def test_default_when_no_match(self):
        node = _make_node(data={"condition": ""})
        state = _make_state(input={"text": "随便聊聊"})
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "default"

    @pytest.mark.asyncio
    async def test_english_keywords(self):
        node = _make_node(data={"condition": "auto"})
        state = _make_state(input={"text": "please translate this"})
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "translate"

    @pytest.mark.asyncio
    async def test_uses_last_message_content(self):
        node = _make_node(data={"condition": "auto"})
        state = _make_state(
            input={"text": ""},
            messages=[HumanMessage(content="请分析这个数据")]
        )
        result = await ConditionNodeExecutor(node).execute(state)
        assert result["context"]["condition_result"] == "analyze"


# ── TTSNodeExecutor ──────────────────────────────────────────────────

class TestTTSNodeExecutor:
    @pytest.mark.asyncio
    async def test_returns_audio_url(self):
        node = _make_node(data={}, node_type="tool_tts")
        state = _make_state(messages=[AIMessage(content="这是要朗读的文本")])
        result = await TTSNodeExecutor(node).execute(state)
        assert "audio_url" in result["node_outputs"]["n1"]
        assert "/api/v1/tts/generate" in result["node_outputs"]["n1"]["audio_url"]

    @pytest.mark.asyncio
    async def test_uses_context_final_output(self):
        node = _make_node(data={}, node_type="tool_tts")
        state = _make_state(
            messages=[],
            context={"final_output": "来自上下文的文本"}
        )
        result = await TTSNodeExecutor(node).execute(state)
        assert "来自上下文" in result["node_outputs"]["n1"]["text"]


# ── TransformNodeExecutor ────────────────────────────────────────────

class TestTransformNodeExecutor:
    @pytest.mark.asyncio
    async def test_json_extract(self):
        node = _make_node(data={
            "transformType": "json_extract",
            "jsonPath": "$.name",
            "inputSource": "last",
        })
        state = _make_state(context={"last_response": '{"name": "Alice", "age": 30}'})
        result = await TransformNodeExecutor(node).execute(state)
        assert result["context"]["transformed_data"] == "Alice"

    @pytest.mark.asyncio
    async def test_json_extract_nested(self):
        node = _make_node(data={
            "transformType": "json_extract",
            "jsonPath": "$.user.city",
            "inputSource": "last",
        })
        state = _make_state(context={"last_response": '{"user": {"city": "Beijing"}}'})
        result = await TransformNodeExecutor(node).execute(state)
        assert result["context"]["transformed_data"] == "Beijing"

    @pytest.mark.asyncio
    async def test_text_slice(self):
        node = _make_node(data={
            "transformType": "text_slice",
            "startIndex": 0,
            "endIndex": 5,
            "inputSource": "last",
        })
        state = _make_state(context={"last_response": "Hello World"})
        result = await TransformNodeExecutor(node).execute(state)
        assert result["context"]["transformed_data"] == "Hello"

    @pytest.mark.asyncio
    async def test_regex_extract(self):
        node = _make_node(data={
            "transformType": "regex_extract",
            "pattern": r"\d+",
            "inputSource": "last",
        })
        state = _make_state(context={"last_response": "Order 12345 confirmed"})
        result = await TransformNodeExecutor(node).execute(state)
        assert result["context"]["transformed_data"] == "12345"

    @pytest.mark.asyncio
    async def test_json_extract_invalid_json(self):
        node = _make_node(data={
            "transformType": "json_extract",
            "jsonPath": "$.key",
            "inputSource": "last",
        })
        state = _make_state(context={"last_response": "not json"})
        result = await TransformNodeExecutor(node).execute(state)
        assert "error" in result["context"]["transformed_data"]


# ── RouterNodeExecutor ──────────────────────────────────────────────

class TestRouterNodeExecutor:
    @pytest.mark.asyncio
    async def test_matches_first_route(self):
        node = _make_node(data={
            "routes": [
                {"keywords": ["翻译", "translate"], "target": "translate_node"},
                {"keywords": ["总结"], "target": "summary_node"},
            ]
        })
        state = _make_state(input={"text": "帮我翻译一下"})
        result = await RouterNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["matched_route"] == "translate_node"

    @pytest.mark.asyncio
    async def test_matches_second_route(self):
        node = _make_node(data={
            "routes": [
                {"keywords": ["翻译"], "target": "translate_node"},
                {"keywords": ["总结", "摘要"], "target": "summary_node"},
            ]
        })
        state = _make_state(input={"text": "帮我摘要"})
        result = await RouterNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["matched_route"] == "summary_node"

    @pytest.mark.asyncio
    async def test_default_when_no_match(self):
        node = _make_node(data={
            "routes": [{"keywords": ["翻译"], "target": "t"}]
        })
        state = _make_state(input={"text": "今天天气怎么样"})
        result = await RouterNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["matched_route"] == "default"

    @pytest.mark.asyncio
    async def test_empty_routes(self):
        node = _make_node(data={"routes": []})
        state = _make_state(input={"text": "anything"})
        result = await RouterNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["matched_route"] == "default"


# ── CodeExecuteNodeExecutor ──────────────────────────────────────────

class TestCodeExecuteNodeExecutor:
    @pytest.mark.asyncio
    async def test_code_node_disabled_by_default(self):
        node = _make_node(data={"code": "result = 2 + 3", "language": "python"})
        state = _make_state()
        with pytest.raises(ValueError, match="代码执行节点未启用"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_simple_code(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_WORKFLOW_CODE_NODES", True)
        node = _make_node(data={"code": "result = 2 + 3", "language": "python"})
        state = _make_state()
        result = await CodeExecuteNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["result"] == "5"

    @pytest.mark.asyncio
    async def test_empty_code_raises(self):
        node = _make_node(data={"code": "", "language": "python"})
        state = _make_state()
        with pytest.raises(ValueError, match="需要提供代码"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_non_python_raises(self):
        node = _make_node(data={"code": "print(1)", "language": "javascript"})
        state = _make_state()
        with pytest.raises(ValueError, match="仅支持 python"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_import_blocked(self):
        node = _make_node(data={"code": "import os\nresult = 1", "language": "python"})
        state = _make_state()
        with pytest.raises(ValueError, match="不允许 import"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_exec_blocked(self):
        node = _make_node(data={"code": "exec('result = 1')", "language": "python"})
        state = _make_state()
        with pytest.raises(ValueError, match="不允许调用 exec"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_syntax_error(self):
        node = _make_node(data={"code": "def (", "language": "python"})
        state = _make_state()
        with pytest.raises(ValueError, match="语法错误"):
            await CodeExecuteNodeExecutor(node).execute(state)

    @pytest.mark.asyncio
    async def test_access_to_state_context(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_WORKFLOW_CODE_NODES", True)
        node = _make_node(data={
            "code": "result = context.get('x', 0) + 10",
            "language": "python",
        })
        state = _make_state(context={"x": 5})
        result = await CodeExecuteNodeExecutor(node).execute(state)
        assert result["node_outputs"]["n1"]["result"] == "15"


# ── NodeExecutor retry ──────────────────────────────────────────────

class TestNodeExecutorRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        node = _make_node()
        executor = InputNodeExecutor(node)
        state = _make_state(input={"text": "hi"})
        result = await executor.execute_with_retry(state)
        assert "messages" in result

    @pytest.mark.asyncio
    async def test_raises_not_implemented_for_base_class(self):
        node = _make_node()
        from app.services.workflow_engine import NodeExecutor
        with pytest.raises(NotImplementedError):
            await NodeExecutor(node).execute(_make_state())


# ── WorkflowEngine ───────────────────────────────────────────────────

class TestWorkflowEngine:
    def test_get_executor_known_types(self):
        config = WorkflowConfig(nodes=[
            _make_node("n1", "input"),
            _make_node("n2", "output"),
            _make_node("n3", "condition"),
            _make_node("n4", "tool_tts"),
            _make_node("n5", "code"),
            _make_node("n6", "transform"),
            _make_node("n7", "router"),
        ], edges=[])
        engine = WorkflowEngine(config)
        for node in config.nodes:
            executor = engine.get_executor(node)
            assert executor is not None

    def test_get_executor_unknown_type_raises(self):
        config = WorkflowConfig(nodes=[
            _make_node("n1", "nonexistent"),
        ], edges=[])
        engine = WorkflowEngine(config)
        with pytest.raises(ValueError, match="Unknown node type"):
            engine.get_executor(config.nodes[0])

    @pytest.mark.asyncio
    async def test_emit_event_with_callback(self):
        config = WorkflowConfig(nodes=[], edges=[])
        engine = WorkflowEngine(config)
        callback = AsyncMock()
        engine.set_event_callback(callback)
        await engine.emit_event("test", {"key": "value"})
        callback.assert_called_once_with("test", {"key": "value"})

    @pytest.mark.asyncio
    async def test_emit_event_without_callback(self):
        config = WorkflowConfig(nodes=[], edges=[])
        engine = WorkflowEngine(config)
        # Should not raise
        await engine.emit_event("test", {})
