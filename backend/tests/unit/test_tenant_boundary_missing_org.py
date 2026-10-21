"""A2 预审建议③：入口**省略** organization_id 必须失败（fail-closed 钉测）。

默认值从 1 改为 None 后，最容易回流的形态是「函数体没做 None 校验」——
调用方漏传时静默以 None 参与查询（ES term "None" / SQL IS NULL / 命名空间 "None:…"），
而不是明确失败。本文件逐类钉住：工具返回结构化失败，服务/构造器抛 ValueError。
"""

from unittest.mock import MagicMock

import pytest

from app.agent.core_tools import (
    get_prompt_template,
    list_prompt_templates,
    search_knowledge_base,
    vector_search,
)
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.registry import ToolResult
from app.services.graph_rag_service import GraphRAGService


@pytest.mark.asyncio
async def test_core_tools_fail_closed_when_org_omitted():
    results = [
        await search_knowledge_base("q"),
        await vector_search("q"),
        await list_prompt_templates(),
        await get_prompt_template("any"),
    ]
    for result in results:
        assert isinstance(result, ToolResult), f"缺组织必须返回结构化失败: {result!r}"
        assert result.success is False
        assert result.error.code == "organization_required"


def test_memory_bridge_rejects_omitted_org():
    with pytest.raises(ValueError, match="organization_id"):
        AgentMemoryBridge(user_id=1)


@pytest.mark.asyncio
async def test_graph_rag_service_rejects_omitted_org():
    service = GraphRAGService()
    for coro in (
        service.load(),
        service.save(),
        service.clear(),
        service.get_analytics(),
        service.build_graph_from_entities([]),
    ):
        with pytest.raises(ValueError, match="organization_id"):
            await coro


@pytest.mark.asyncio
async def test_agent_service_chat_rejects_omitted_org():
    from app.agent.service import AgentService

    service = AgentService(openai_client=MagicMock())
    agen = service.chat("hello")
    with pytest.raises(ValueError, match="organization_id"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_subagent_rejects_omitted_org():
    from app.agent import subagent

    gen = subagent.delegate_task(MagicMock(), "task")
    with pytest.raises(ValueError, match="organization_id"):
        await gen.__anext__()
