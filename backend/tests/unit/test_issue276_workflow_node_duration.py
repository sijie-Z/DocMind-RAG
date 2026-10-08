"""issue #276：workflow 引擎首节点即崩 —— NodeResult 缺 duration 字段而引擎直接赋值。

背景：`build_graph.node_func` 在节点完成（:772）与失败（:791）两条路径都写
`self.node_results[id].duration = duration`，而 `NodeResult` 未声明该字段；
pydantic v2 对未声明字段赋值抛 `ValueError: "NodeResult" object has no field
"duration"` → 任何真实工作流图在首个节点完成时即崩、整图中止（workflow_error）。

修复：`NodeResult` 补 `duration: int | None = None`（毫秒；与引擎
`int((completed_at - started_at).total_seconds() * 1000)` 的写入类型、前端
`${duration}ms` 的渲染口径一致）。

测试盲区说明：既有 test_workflow_engine.py 只直接调各 executor.execute() /
execute_with_retry()，从未编译或执行 LangGraph 图（无一处调用 engine.execute()
或 build_graph().compile()），赋值点 :772/:791 完全无覆盖——因此此前全绿。

变异判别：删除 `NodeResult.duration` 声明 → 本文件用例按
`ValueError: "NodeResult" object has no field "duration"` 精确红。
"""
import pytest

from app.schemas.workflow import WorkflowConfig, WorkflowEdge, WorkflowNode
from app.services.workflow_engine import WorkflowEngine


def _minimal_config() -> WorkflowConfig:
    """input → output 最小图：不触达 LLM/知识库等外部依赖。"""
    return WorkflowConfig(
        nodes=[
            WorkflowNode(id="in1", type="input", data={"prompt": "{{input}}"}, position={"x": 0, "y": 0}),
            WorkflowNode(id="out1", type="output", data={}, position={"x": 1, "y": 0}),
        ],
        edges=[WorkflowEdge(id="e1", source="in1", target="out1")],
    )


def _initial_state(text: str = "hello") -> dict:
    return {
        "input": {"text": text},
        "messages": [],
        "current_node": None,
        "node_outputs": {},
        "context": {},
        "errors": [],
        "memory": {},
        "iteration_count": 0,
        "tool_results": [],
    }


class TestEngineGraphCompletesFirstNode:
    @pytest.mark.asyncio
    async def test_first_node_completes_and_records_duration(self):
        """真实引擎编译图直跑：input→output 两节点 completed，duration 为 int 毫秒。

        刻意不经 engine.execute()（它会吞掉异常、把崩溃转成 success=False）；
        缺字段形态在本用例中按原始 ValueError 精确红。
        """
        engine = WorkflowEngine(_minimal_config(), organization_id=1)
        compiled = engine.build_graph().compile()

        await compiled.ainvoke(_initial_state())

        results = {r.node_id: r for r in engine.get_node_results()}
        assert set(results) == {"in1", "out1"}
        assert results["in1"].status == "completed"
        assert results["out1"].status == "completed"
        assert isinstance(results["in1"].duration, int)
        assert results["in1"].duration >= 0
        assert results["in1"].started_at is not None
        assert results["in1"].completed_at is not None

    @pytest.mark.asyncio
    async def test_engine_execute_public_path_reports_success(self):
        """engine.execute() 公共路径：success=True 且 node_results 全部携带 duration。"""
        engine = WorkflowEngine(_minimal_config(), organization_id=1)
        result = await engine.execute({"text": "hello"})

        assert result["success"] is True, result
        durations = [r.duration for r in engine.get_node_results()]
        assert len(durations) == 2
        assert all(isinstance(d, int) and d >= 0 for d in durations)


class TestFailedNodeDuration:
    @pytest.mark.asyncio
    async def test_failed_node_records_duration_and_error(self):
        """失败路径（:791）：确定性错误节点 status=failed、error 保留、duration 已写，图不中止。

        code 节点默认未启用（ENABLE_WORKFLOW_CODE_NODES=False）→ 执行时抛确定性 ValueError。
        max_retries=1：即使（如变异所还原）退回"一切皆重试"的旧行为，也不产生退避等待。
        """
        config = WorkflowConfig(
            nodes=[WorkflowNode(id="code1", type="code", data={"code": "result = 1", "language": "python"})],
            edges=[],
        )
        engine = WorkflowEngine(config, llm_config={"max_retries": 1})
        compiled = engine.build_graph().compile()

        await compiled.ainvoke(_initial_state())  # 失败被 node_func 收编为该节点 failed，图继续

        (res,) = engine.get_node_results()
        assert res.status == "failed"
        assert "代码执行节点未启用" in res.error
        assert isinstance(res.duration, int)
        assert res.duration >= 0
