"""issue #509：低风险 utility 伞形行为与旧名兼容。

覆盖：
- utility 的 5 个 operation 各自调用独立 handler（不经 execute_python）；
- 旧名（calculate / format_converter / get_current_time / detect_language /
  analyze_data / list_skills / vector_search / fetch_webpage / summarize_document）
  经 TOOL_ALIASES 仍可用；
- disabled_tools 里写旧名同样挡住规范工具（别名不得绕过禁用清单）。
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.registry import tool_registry
from app.agent.tools.utility import utility


@pytest.mark.asyncio
async def test_utility_operations_use_independent_handlers():
    assert await utility(operation="calculate", expression="2 + 2") == "4"

    converted = await utility(operation="format_converter", data='{"a": 1}', output_format="json")
    assert '"a": 1' in converted

    now = await utility(operation="get_current_time")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", now)

    language = await utility(operation="detect_language", text="这是一段中文文本，用于语言检测。")
    assert "中文" in language or "zh" in language

    stats = json.loads(await utility(operation="analyze_data", data="[1, 2, 3, 4]"))
    assert stats["count"] == 4 and stats["mean"] == 2.5


@pytest.mark.asyncio
async def test_utility_never_calls_execute_python():
    """伞形工具是独立 handler，不得把低危操作委托给 high 的 execute_python。"""
    with patch(
        "app.agent.tools.code_execution.execute_python", new=AsyncMock()
    ) as execute_python_spy:
        await utility(operation="calculate", expression="1 + 1")
        await utility(operation="analyze_data", data="[1, 2, 3]")
        await utility(operation="detect_language", text="hello world")
        execute_python_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_utility_validates_operation_and_required_args():
    bad = await utility(operation="nope")
    assert not bad.success and bad.error.code == "validation_error"

    missing = await utility(operation="calculate")
    assert not missing.success and missing.error.code == "validation_error"


@pytest.mark.asyncio
async def test_old_names_route_to_canonical_tools(monkeypatch):
    """旧名经 registry.execute_detailed 仍可用（参数默认值与旧语义一致）。"""
    # get_current_time → utility
    result = await tool_registry.execute_detailed("get_current_time", {})
    assert result.success
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(result))

    # calculate → utility
    result = await tool_registry.execute_detailed("calculate", {"expression": "3 * 3"})
    assert str(result) == "9"

    # analyze_data → utility
    result = await tool_registry.execute_detailed("analyze_data", {"data": "[1, 2, 3]"})
    assert json.loads(str(result))["count"] == 3

    # list_skills → load_skill_reference（skill_name 缺省=列表）
    result = await tool_registry.execute_detailed("list_skills", {})
    assert result.success and "可用技能" in str(result)


@pytest.mark.asyncio
async def test_merged_search_and_summary_aliases_route_to_impl(monkeypatch):
    """vector_search / summarize_document 旧名路由到规范工具的对应实现。"""
    from app.agent import core_tools

    vector_impl = AsyncMock(return_value="vector-ok")
    with patch.object(core_tools, "_vector_search_impl", vector_impl):
        result = await tool_registry.execute_detailed(
            "vector_search", {"query": "q", "top_k": 5}, organization_id=1
        )
    assert result.success and str(result) == "vector-ok"
    vector_impl.assert_awaited_once_with("q", 5, 1, None)

    summary_impl = AsyncMock(return_value="summary-ok")
    with patch.object(core_tools, "_summarize_document_impl", summary_impl):
        result = await tool_registry.execute_detailed(
            "summarize_document", {"document_id": "doc-1"}, organization_id=1
        )
    assert result.success and str(result) == "summary-ok"
    summary_impl.assert_awaited_once_with("doc-1", 1, None)


@pytest.mark.asyncio
async def test_fetch_webpage_alias_routes_to_impl(monkeypatch):
    from app.agent.tools import web_search as web_search_module

    fetch_impl = AsyncMock(return_value="page-ok")
    with patch.object(web_search_module, "_fetch_webpage_impl", fetch_impl):
        result = await tool_registry.execute_detailed(
            "fetch_webpage", {"url": "https://example.com"}, user_id=7
        )
    assert result.success and str(result) == "page-ok"
    fetch_impl.assert_awaited_once_with("https://example.com", 7)


@pytest.mark.asyncio
async def test_search_vector_mode_honors_document_ids(monkeypatch):
    """规范入口的 document_ids 在 vector 模式下不得被静默丢弃。"""
    from app.agent import core_tools

    captured: dict = {}

    async def _fake_impl(query, top_k, organization_id, document_ids=None):
        captured.update(
            query=query, top_k=top_k, organization_id=organization_id, document_ids=document_ids
        )
        return "ok"

    with patch.object(core_tools, "_vector_search_impl", _fake_impl):
        result = await tool_registry.execute_detailed(
            "search_knowledge_base",
            {"query": "q", "mode": "vector", "document_ids": ["d1", "d2"]},
            organization_id=1,
        )
    assert result.success
    assert captured["document_ids"] == ["d1", "d2"]


@pytest.mark.asyncio
async def test_summary_mode_rejects_insights_only_arguments():
    """mode=summary 传 aspects/framework 必须显式拒绝，不得静默忽略。"""
    result = await tool_registry.execute_detailed(
        "extract_insights",
        {"document_id": "doc-1", "mode": "summary", "framework": "swot"},
        organization_id=1,
    )
    assert not result.success
    assert result.error.code == "validation_error"


def test_disabled_tools_old_name_blocks_old_call_not_whole_umbrella():
    """禁用清单写旧名只阻断旧名调用；不隐藏整个规范伞形（issue #509 评审修正）。"""
    cfg = AgentConfig(disabled_tools=["get_current_time"])
    names = {t["function"]["name"] for t in tool_registry.build_tool_schemas(cfg)}
    assert "utility" in names, "旧名禁用不得把整个 utility 伞形隐藏"

    executor = Executor(MagicMock(), cfg, AgentMemoryBridge(agent_id="t", organization_id=1))
    old_call = Executor._canonicalize_tool_call(
        SimpleNamespace(
            id="c1",
            function=SimpleNamespace(name="get_current_time", arguments="{}"),
        )
    )
    canonical_call = SimpleNamespace(
        id="c2",
        function=SimpleNamespace(
            name="utility", arguments=json.dumps({"operation": "get_current_time"})
        ),
    )
    assert executor._call_disabled(old_call) is True, "旧名调用必须被禁用清单阻断"
    assert executor._call_disabled(canonical_call) is False
