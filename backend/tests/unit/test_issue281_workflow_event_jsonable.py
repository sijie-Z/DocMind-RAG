"""issue #281：引擎事件载荷 JSON 化 —— 流式路径的 node_complete 不再携带不可序列化对象。

背景（评审 #280 §7 实测）：流式端点事件泵对事件载荷直接 `json.dumps`，而引擎级
`node_complete` 事件携带 executor 原始返回（input 节点必含 `messages: [HumanMessage]`）
→ `TypeError: Object of type HumanMessage is not JSON serializable` → SSE 只到 error、
执行记录永卡 running、node_results 不落库。

修复：引擎出口统一规整（`_to_jsonable`）——
1. `emit_event`：全部事件类型（含 node_start/node_error/workflow_error 等）的载荷
   在回调边界前规整，是单一收口点；
2. `build_graph.node_func`：完成路径把 output 规整后再落 `NodeResult`（落库/响应/
   事件三处同形态）；
3. `execute()` 返回值：规整（workflow_result 事件 + output_data JSON 列 + 非流式响应）。

变异判别：
- 还原 `emit_event` 裸传（去掉 _to_jsonable）→ 本节全图/边界两用例按
  `TypeError: Object of type HumanMessage is not JSON serializable` 精确红；
- 还原 `node_func` 的 output 规整 → `NodeResult.output` 回到消息对象、事件/落库形态
  漂移 → 全图用例红。
"""

import json
from datetime import datetime

import pytest
from langchain_core.messages import HumanMessage

from app.schemas.workflow import WorkflowConfig, WorkflowEdge, WorkflowNode
from app.services.workflow_engine import WorkflowEngine, _to_jsonable


def _minimal_config() -> WorkflowConfig:
    """input → output 最小图：不触达 LLM/知识库等外部依赖。"""
    return WorkflowConfig(
        nodes=[
            WorkflowNode(
                id="in1", type="input", data={"prompt": "{{input}}"}, position={"x": 0, "y": 0}
            ),
            WorkflowNode(id="out1", type="output", data={}, position={"x": 1, "y": 0}),
        ],
        edges=[WorkflowEdge(id="e1", source="in1", target="out1")],
    )


class _Collector:
    """收集事件回调的 (event_type, data) 序列。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, data: dict):
        self.events.append((event_type, data))


class TestToJsonable:
    def test_covers_messages_containers_datetime_and_fallback(self):
        """规整口径：消息 → dict、容器递归、datetime → isoformat、未知对象 → str。"""
        value = {
            "messages": [HumanMessage(content="你好")],
            "nested": {"pair": (1, 2)},
            "at": datetime(2026, 1, 2, 3, 4, 5),
            "obj": object(),
        }
        out = _to_jsonable(value)

        json.dumps(out, ensure_ascii=False)  # 规整后必可 dumps，不抛
        assert out["messages"][0]["content"] == "你好"
        assert out["messages"][0]["type"] == "human"
        assert out["nested"]["pair"] == [1, 2]
        assert out["at"] == "2026-01-02T03:04:05"
        assert isinstance(out["obj"], str)
        # dict 键统一 str 化（评审 V3：去 str(k) 变异曾全绿存活——JSON 对象键
        # 必须为字符串；int/tuple 等非 str 键都必须转换）。
        assert _to_jsonable({1: "a", (2, 3): "b"}) == {"1": "a", "(2, 3)": "b"}

    def test_primitives_pass_through_unchanged(self):
        for v in [None, True, 0, 1.5, "s"]:
            assert _to_jsonable(v) == v


class TestEngineEventsJsonSerializable:
    @pytest.mark.asyncio
    async def test_emit_event_normalizes_payload_at_callback_boundary(self):
        """回调解边界的单一收口点：任意载荷（含消息对象）经 emit_event 后必可 JSON 化。"""
        engine = WorkflowEngine(WorkflowConfig(nodes=[], edges=[]))
        collector = _Collector()
        engine.set_event_callback(collector)

        await engine.emit_event(
            "custom_event",
            {
                "message": HumanMessage(content="边界载荷"),
                "obj": object(),
            },
        )

        (event_type, data) = collector.events[0]
        assert event_type == "custom_event"
        json.dumps(data, ensure_ascii=False)  # 关键断言：端点泵原始 dumps 不抛
        assert data["message"]["content"] == "边界载荷"
        assert isinstance(data["obj"], str)

    @pytest.mark.asyncio
    async def test_full_graph_happy_path_events_all_json_serializable(self):
        """真实编译图 input→output：每个事件载荷都能被端点泵原样 json.dumps。

        修复前 node_complete 的 output.messages[0] 是 HumanMessage 对象 →
        json.dumps 抛 TypeError，本用例红。
        """
        engine = WorkflowEngine(_minimal_config(), organization_id=1)
        collector = _Collector()
        engine.set_event_callback(collector)

        result = await engine.execute({"text": "你好"})

        assert result["success"] is True, result
        for event_type, data in collector.events:
            json.dumps({"event": event_type, "data": data}, ensure_ascii=False)

        by_type = {event_type for event_type, _ in collector.events}
        assert {"workflow_start", "node_start", "node_complete", "workflow_complete"} <= by_type

        in1 = next(
            data
            for event_type, data in collector.events
            if event_type == "node_complete" and data["node_id"] == "in1"
        )
        msg = in1["output"]["messages"][0]
        assert msg["content"] == "你好"
        assert msg["type"] == "human"

    @pytest.mark.asyncio
    async def test_node_results_output_uses_same_jsonable_form(self):
        """node_func 落 NodeResult 的 output 与事件载荷同形态：已是 dict 而非消息对象。

        还原 output 规整 → `res.output["messages"][0]` 是 HumanMessage，
        下标访问即 TypeError，本用例红。
        """
        engine = WorkflowEngine(_minimal_config(), organization_id=1)
        result = await engine.execute({"text": "你好"})
        assert result["success"] is True, result

        results = {r.node_id: r for r in engine.get_node_results()}
        msg = results["in1"].output["messages"][0]  # 键访问即判别（消息对象不可下标）
        assert msg["content"] == "你好"
        assert msg["type"] == "human"
        # 落库口径（mode="json"）逐字段可 JSON 化且与事件形态一致
        dumped = results["in1"].model_dump(mode="json")
        assert dumped["output"]["messages"][0]["content"] == "你好"

    @pytest.mark.asyncio
    async def test_node_error_and_workflow_error_events_json_serializable(self):
        """失败事件族（node_error / workflow_error）载荷同样可 JSON 化。

        node_error：code 节点空代码 → 确定性 ValueError，被 node_func 收编；
        workflow_error：未知节点类型在 get_executor 处抛出 → 整图异常被 execute 收编。
        """
        # ── node_error ──
        node_error_config = WorkflowConfig(
            nodes=[
                WorkflowNode(
                    id="in1", type="input", data={"prompt": "{{input}}"}, position={"x": 0, "y": 0}
                ),
                WorkflowNode(id="code1", type="code", data={"code": ""}, position={"x": 1, "y": 0}),
                WorkflowNode(id="out1", type="output", data={}, position={"x": 2, "y": 0}),
            ],
            edges=[
                WorkflowEdge(id="e1", source="in1", target="code1"),
                WorkflowEdge(id="e2", source="code1", target="out1"),
            ],
        )
        engine = WorkflowEngine(node_error_config, organization_id=1)
        collector = _Collector()
        engine.set_event_callback(collector)
        await engine.execute({"text": "hello"})

        by_type = {event_type for event_type, _ in collector.events}
        assert "node_error" in by_type
        for event_type, data in collector.events:
            json.dumps({"event": event_type, "data": data}, ensure_ascii=False)

        # ── workflow_error（未知节点类型 → 入口节点 get_executor 抛出）──
        bad_config = WorkflowConfig(
            nodes=[
                WorkflowNode(id="n0", type="bogus_type_281", data={}, position={"x": 0, "y": 0}),
                WorkflowNode(id="out1", type="output", data={}, position={"x": 1, "y": 0}),
            ],
            edges=[WorkflowEdge(id="e1", source="n0", target="out1")],
        )
        engine2 = WorkflowEngine(bad_config, organization_id=1)
        collector2 = _Collector()
        engine2.set_event_callback(collector2)
        result2 = await engine2.execute({"text": "hello"})

        assert result2["success"] is False
        assert "Unknown node type" in result2["error"]
        by_type2 = {event_type for event_type, _ in collector2.events}
        assert "workflow_error" in by_type2
        for event_type, data in collector2.events:
            json.dumps({"event": event_type, "data": data}, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_execute_return_value_normalized_for_workflow_result_event(self):
        """execute() 返回值同口径规整：端点对 workflow_result 载荷原样 json.dumps
        （另落 output_data JSON 列）——自定义 executor 往 node_outputs 塞消息对象，
        返回值必须仍是可 JSON 化形态。

        还原 execute() 的 `_to_jsonable(...)` 包装 → 下方 json.dumps(result) 抛
        `TypeError: Object of type HumanMessage is not JSON serializable`，本用例红。
        """
        from app.services.workflow_engine import NodeExecutor

        class _StubExecutor(NodeExecutor):
            async def execute(self, state):
                return {
                    "node_outputs": {"s1": {"msg": HumanMessage(content="结果消息")}},
                    "context": {"last_response": "ok"},
                }

        class _StubEngine(WorkflowEngine):
            def get_executor(self, node):
                return _StubExecutor(node, {})

        config = WorkflowConfig(
            nodes=[WorkflowNode(id="s1", type="output", data={}, position={"x": 0, "y": 0})],
            edges=[],
        )
        engine = _StubEngine(config, organization_id=1)
        result = await engine.execute({"text": "hi"})

        assert result["success"] is True, result
        json.dumps(result, ensure_ascii=False)  # 关键断言：端点对返回值原始 dumps 不抛
        assert result["node_outputs"]["s1"]["msg"]["content"] == "结果消息"
