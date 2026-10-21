"""R0 回归测试：工作流知识库检索的租户范围必须由服务端推导。

修复前：KnowledgeSearchNodeExecutor 从 state["input"]["organization_id"] 读取检索组织，
而 input 是未校验的请求体，任意认证用户可跨租户读取知识库。
修复后：组织由 API 层从认证用户推导、经 WorkflowEngine 显式注入，
请求体中的同名字段被忽略。

issue #268 补充：注入值本身不再 `or 1` 兜底——API 层如实传调用者的组织（可能为
None）；引擎/节点对缺组织**显式失败**（此前 `else 1` 会把缺组织的检索静默放进
1 号组织，而检索结果随执行输出返回 = 跨租户泄漏）。
"""

import pytest

from app.schemas.workflow import WorkflowConfig, WorkflowNode
from app.services.workflow_engine import (
    KnowledgeSearchNodeExecutor,
    WorkflowEngine,
    WorkflowState,
)


def _make_node(
    node_id: str = "n1", node_type: str = "tool_search", data: dict | None = None
) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, data=data or {}, position={"x": 0, "y": 0})


def _make_state(**overrides) -> WorkflowState:
    base: WorkflowState = {
        "input": {"text": "报销流程"},
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


class TestWorkflowRetrievalOrgScope:
    @pytest.mark.asyncio
    async def test_client_supplied_organization_id_is_ignored(self, monkeypatch):
        """请求体里的 organization_id=42 不得影响实际检索组织。"""
        from app.services.rag_service import RagServiceFacade

        calls: list[int] = []

        # 类方法替身：首参 self 承接描述符绑定（类级 patch 的既有语义）
        async def fake_search(self, query, organization_id, top_k=5, **kwargs):
            calls.append(organization_id)
            return [{"content": "命中内容", "score": 0.9, "title": "资料"}], None

        # 必须打在**类**上。此前打单例实例（monkeypatch.setattr(rag_service, …)）：
        # monkeypatch 撤销时经 hasattr 把「绑定方法」写回成实例属性（实测确认
        # `'search_knowledge_base' in rag_service.__dict__` 为 True）→ 该单例被永久
        # shadow，此后所有对类方法的 patch（如故障注入用例）静默失效 —— 单独跑绿、
        # 整包同进程跑红（issue #292 CI 红的一次根因）。类级 patch 覆盖同一单例，
        # 语义不变、撤销干净。
        monkeypatch.setattr(RagServiceFacade, "search_knowledge_base", fake_search)

        executor = KnowledgeSearchNodeExecutor(_make_node())
        executor.organization_id = 7  # 服务端注入（引擎从认证用户推导）
        state = _make_state(input={"text": "报销流程", "organization_id": 42})

        result = await executor.execute(state)

        assert calls == [7], "检索必须使用服务端注入的组织，而不是请求体中的 organization_id"
        assert result["context"]["knowledge_context"] == "命中内容"

    @pytest.mark.asyncio
    async def test_executor_without_injected_org_refuses_not_org1(self, monkeypatch):
        """引擎未注入组织时不得退回 1 号组织（issue #268）——显式失败、不检索任何组织。

        修复前这里是 `... else 1`：检索结果会随执行输出返回，缺组织时静默检索 1 号
        组织 = 跨租户泄漏。现在缺组织即抛错（与委派子代理的 fail-closed 同风格）。

        变异验证：把 `if organization_id is None: raise ...` 改回 `else 1` → 本用例红。
        """
        from app.services.rag_service import RagServiceFacade

        calls: list[int] = []

        # 类方法替身：首参 self 承接描述符绑定（类级 patch 的既有语义）
        async def fake_search(self, query, organization_id, top_k=5, **kwargs):
            calls.append(organization_id)
            return [], None

        # 类级 patch：理由同上（实例级 patch 的绑定方法回写会永久 shadow 单例）。
        monkeypatch.setattr(RagServiceFacade, "search_knowledge_base", fake_search)

        executor = KnowledgeSearchNodeExecutor(_make_node())
        with pytest.raises(ValueError):
            await executor.execute(_make_state(input={"text": "x", "organization_id": 42}))

        assert calls == [], "缺组织时不得检索任何组织（bug 形态: 被伪造为 org 1 检索）"

    def test_engine_injects_organization_id_into_search_executor(self):
        """WorkflowEngine(organization_id=...) 必须把组织注入 tool_search 执行器。"""
        config = WorkflowConfig(nodes=[_make_node("search1", "tool_search")], edges=[])
        engine = WorkflowEngine(config, organization_id=7)
        executor = engine.get_executor(config.nodes[0])

        assert isinstance(executor, KnowledgeSearchNodeExecutor)
        assert executor.organization_id == 7

    def test_engine_does_not_leak_org_into_other_executors(self):
        """注入只作用于知识库检索节点，不影响其他执行器构造。"""
        config = WorkflowConfig(nodes=[_make_node("in1", "input")], edges=[])
        engine = WorkflowEngine(config, organization_id=7)
        executor = engine.get_executor(config.nodes[0])
        assert not hasattr(executor, "organization_id")
