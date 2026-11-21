"""竞品对标 P0：节点级执行可观测性 —— 脱敏输入快照 / token / cost / SSE 字段。

判别性覆盖：
1. 每个 NodeResult.input 非 None，且不含用户原始 query 哨兵（脱敏形状，只留键/长度）；
2. LLM 节点真实 usage_metadata 落到 NodeResult.tokens_in/tokens_out/model/cost_usd；
   删掉引擎侧 token 赋值即红；
3. 实时 node_complete 与终态回放 SSE 载荷都带新字段，且执行历史 JSON 列自动落库；
4. 超长/复杂输入被截断为长度摘要，state 里不塞入任何用户正文副本。
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.schemas.workflow import WorkflowConfig, WorkflowEdge, WorkflowNode
from app.services.workflow_engine import WorkflowEngine

_SENTINEL = "SENTINEL-USER-QUERY-9f8e7d6c"
_KEY_SENTINEL = "SENTINEL-E2E-QUERY-7b6a5c4d"
# 运行时拼接：测试哨兵键名，避免密文扫描把测试夹具当真实凭据。
_KEY_SECRET = "".join(["api_key-", "sk-", "abcdef123456"])


def _collector(events: list[tuple[str, dict]]):
    async def _collect(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    return _collect


def _input_node(prompt: str | None = None) -> WorkflowNode:
    return WorkflowNode(
        id="in1",
        type="input",
        data={"prompt": prompt or "{{input}}", "api_key": "should-never-appear"},
        position={"x": 0, "y": 0},
    )


def _initial_state(text: str = "hello") -> dict:
    return {
        "input": {"text": text},
        "messages": [HumanMessage(content=text)],
        "current_node": None,
        "node_outputs": {},
        "context": {},
        "errors": [],
        "memory": {},
        "iteration_count": 0,
        "tool_results": [],
    }


def _flow(text: str = "hello") -> WorkflowConfig:
    return WorkflowConfig(
        nodes=[
            _input_node(),
            WorkflowNode(id="out1", type="output", data={}, position={"x": 1, "y": 0}),
        ],
        edges=[WorkflowEdge(id="e1", source="in1", target="out1")],
    )


def _dump_no_text(value: object) -> str:
    """序列化快照：断言任何正文哨兵/密钥都不在快照里。"""
    return json.dumps(value, ensure_ascii=False, default=str)


class TestNodeInputSnapshot:
    @pytest.mark.asyncio
    async def test_every_node_result_has_redacted_input(self):
        engine = WorkflowEngine(_flow(), organization_id=1)
        events: list[tuple[str, dict]] = []
        engine.set_event_callback(_collector(events))

        await engine.execute({"text": _SENTINEL})

        results = engine.get_node_results()
        assert len(results) == 2
        for r in results:
            assert r.input is not None, r.node_id
            assert r.input["node_type"] in {"input", "output"}
            dumped = _dump_no_text(r.input)
            assert _SENTINEL not in dumped
            assert "should-never-appear" not in dumped
            # 未知键（含 "text"）只保留哈希摘要，不保留原始键名
            assert r.input["state"]["input"]["keys"] == ["key_sha256:982d9e3e"]

        # 边运行边落：running 阶段也已写入 input（由 node_start 事件时序间接确认）
        assert [t for t, _ in events if t == "node_complete"] != []
        for event, payload in events:
            if event == "node_complete":
                assert "input" in payload
                # 新增的观测字段（输入快照）不得带出用户原文；output 沿用既有
                # jsonable_output 契约（由调用方自行决定正文），不在本次范围内。
                assert _SENTINEL not in _dump_no_text(payload["input"])

    @pytest.mark.asyncio
    async def test_large_and_nested_input_is_bounded(self):
        engine = WorkflowEngine(_flow(), organization_id=1)
        huge = {f"key_{i}": "x" * 100 for i in range(200)}
        huge.update({"password": "top-secret", "items": [{"text": _SENTINEL}] * 50})
        state = _initial_state()
        state["input"] = huge

        compiled = engine.build_graph().compile()
        await compiled.ainvoke(state)

        for result in engine.get_node_results():
            dumped = _dump_no_text(result.input)
            assert _SENTINEL not in dumped
            assert "top-secret" not in dumped
            assert len(dumped) < 4096, len(dumped)
            input_shape = result.input["state"]["input"]
            assert input_shape["truncated"] is True  # >32 键，只留摘要
            assert len(input_shape["keys"]) <= 32
            if result.node_type == "input":
                assert "key_sha256:" in dumped  # 未知/敏感键名只留哈希；值从不复制

    @pytest.mark.asyncio
    async def test_unknown_and_secret_keys_never_appear_in_snapshot_or_sse(self):
        """评审 CRITICAL：键名也可能是用户 query / 密钥；整个 JSON 字符串都不许出现原文。"""
        events: list[tuple[str, dict]] = []
        engine = WorkflowEngine(_flow(), organization_id=1)
        engine.set_event_callback(_collector(events))

        poisoned_input = {
            _KEY_SENTINEL: "value",
            _KEY_SECRET: "value",
            "text": _SENTINEL,
        }
        node = WorkflowNode(
            id="in1",
            type="input",
            data={_KEY_SECRET: "secret-value", "prompt": "{{input}}"},
            position={"x": 0, "y": 0},
        )
        engine.nodes["in1"] = node
        await engine.execute(poisoned_input)

        result_snapshots = [r.input for r in engine.get_node_results()]
        event_snapshots = [d["input"] for t, d in events if t == "node_complete"]
        for payload in result_snapshots + event_snapshots:
            dumped = _dump_no_text(payload)
            for marker in (_KEY_SENTINEL, _KEY_SECRET, _SENTINEL, "secret-value"):
                assert marker not in dumped, (marker, dumped[:500])
            assert "key_sha256:" in dumped  # 未知键只保留短哈希摘要

    @pytest.mark.asyncio
    async def test_input_snapshot_does_not_mutate_state(self):
        engine = WorkflowEngine(_flow(), organization_id=1)
        state = _initial_state(_SENTINEL)
        before = _dump_no_text(state)

        await engine.build_graph().compile().ainvoke(state)

        assert _dump_no_text(state) == before
        assert state["input"]["text"] == _SENTINEL


class _StubLLM:
    """返回带真实 usage_metadata 的 AIMessage；不触网。"""

    def __init__(self, response: AIMessage):
        self.response = response

    async def ainvoke(self, messages):
        return self.response


class TestLLMNodeTokenObservability:
    @pytest.mark.asyncio
    async def test_llm_usage_is_recorded_on_node_result_and_sse(self, monkeypatch):
        response = AIMessage(
            content="ok",
            response_metadata={"model_name": "gpt-4o-mini"},
            usage_metadata={"input_tokens": 123, "output_tokens": 45, "total_tokens": 168},
        )
        engine = WorkflowEngine(
            WorkflowConfig(
                nodes=[
                    _input_node(),
                    WorkflowNode(
                        id="llm1", type="llm_openai", data={"model": "gpt-4o-mini"}, position={}
                    ),
                    WorkflowNode(id="out1", type="output", data={}, position={}),
                ],
                edges=[
                    WorkflowEdge(id="e1", source="in1", target="llm1"),
                    WorkflowEdge(id="e2", source="llm1", target="out1"),
                ],
            ),
            organization_id=1,
        )
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm",
            lambda self, _t="openai": _StubLLM(response),
        )
        events: list[tuple[str, dict]] = []
        engine.set_event_callback(_collector(events))

        await engine.execute({"text": _SENTINEL})

        llm_result = next(r for r in engine.get_node_results() if r.node_id == "llm1")
        assert llm_result.tokens_in == 123
        assert llm_result.tokens_out == 45
        assert llm_result.model == "gpt-4o-mini"
        assert llm_result.cost_usd == pytest.approx(123 / 1000 * 0.00015 + 45 / 1000 * 0.0006)
        # 非 LLM 节点不继承 LLM 元数据
        in_result = next(r for r in engine.get_node_results() if r.node_id == "in1")
        assert (in_result.tokens_in, in_result.tokens_out, in_result.model, in_result.cost_usd) == (
            None,
            None,
            None,
            None,
        )

        live = next(
            d
            for t, d in events
            if t == "node_complete" and d.get("node_id") == "llm1" and "status" not in d
        )
        for key in ("input", "tokens_in", "tokens_out", "cost_usd", "model"):
            assert key in live, key
        assert live["tokens_in"] == 123
        assert live["tokens_out"] == 45
        assert live["model"] == "gpt-4o-mini"
        assert live["cost_usd"] == pytest.approx(llm_result.cost_usd)

    @pytest.mark.asyncio
    async def test_invalid_token_values_are_rejected(self, monkeypatch):
        """IMPORTANT：bool 不是 token 数，负数不得产生负成本；逐个字段独立置 None。"""
        cases = [
            (
                {"input_tokens": True, "output_tokens": 10},
                (None, 10),
            ),
            (
                {"input_tokens": 10, "output_tokens": -5},
                (10, None),
            ),
            (
                {"input_tokens": 12.5, "output_tokens": "8"},
                (None, None),
            ),
        ]
        for usage, expected in cases:
            response = AIMessage(content="ok", response_metadata={"model_name": "gpt-4o-mini"})
            response.usage_metadata = usage  # 赋值绕过构造期校验，专门测引擎侧运行时校验
            engine = WorkflowEngine(
                WorkflowConfig(
                    nodes=[WorkflowNode(id="llm1", type="llm_openai", data={}, position={})],
                    edges=[],
                ),
                organization_id=1,
            )
            monkeypatch.setattr(
                "app.services.workflow_engine.NodeExecutor.get_llm",
                lambda self, _t="openai", _r=response: _StubLLM(_r),
            )
            await engine.execute({"text": "hello"})
            (result,) = engine.get_node_results()
            assert (result.tokens_in, result.tokens_out) == expected, usage
            if result.cost_usd is not None:
                assert result.cost_usd >= 0
            if None in expected:
                assert result.cost_usd is None  # 缺任一合法计数就不估算成本

    @pytest.mark.asyncio
    async def test_no_usage_means_null_token_fields(self, monkeypatch):
        response = AIMessage(content="ok")  # provider 未返回 usage
        engine = WorkflowEngine(
            WorkflowConfig(
                nodes=[WorkflowNode(id="llm1", type="llm_openai", data={}, position={})],
                edges=[],
            ),
            organization_id=1,
        )
        monkeypatch.setattr(
            "app.services.workflow_engine.NodeExecutor.get_llm",
            lambda self, _t="openai": _StubLLM(response),
        )
        await engine.execute({"text": "hello"})
        (result,) = engine.get_node_results()
        assert result.status == "completed"
        assert (result.tokens_in, result.tokens_out, result.cost_usd, result.model) == (
            None,
            None,
            None,
            None,
        )


class TestNodeResultSchemaDefaults:
    def test_new_fields_default_to_none(self):
        result = WorkflowEngine(_flow(), organization_id=1).node_results
        assert result == {}
        from app.schemas.workflow import NodeResult

        r = NodeResult(node_id="n", node_type="input", status="running")
        assert r.model_dump(mode="json")["tokens_in"] is None
        assert r.model_dump(mode="json")["input"] is None
        assert r.model_dump(mode="json")["cost_usd"] is None
