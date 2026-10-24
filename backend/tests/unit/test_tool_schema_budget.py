"""issue #509：工具 schema 预算与注入门控单一来源。

覆盖：
- 默认配置常驻 10–12 个工具、schema ≤1,500 tokens（tiktoken cl100k_base）；
- schema 只发规范名（hidden 旧名与 TOOL_ALIASES 均不出现）；
- loop / executor / planner / ``/agent/tools`` 四个消费方同源；
- terminal / feishu / delegation / 扩展面按开关或显式 tool_tags 注入。
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.loop import PERAgentLoop
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Planner, PlanStep
from app.agent.registry import TOOL_ALIASES, tool_registry
from app.core.config import settings


@pytest.fixture(autouse=True)
def _hermetic_surface(monkeypatch):
    """预算断言基于默认配置：显式隔离本机可能存在的门控开关。"""
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)


DEFAULT_RESIDENT = {
    "search_knowledge_base",
    "web_search",
    "extract_insights",
    "utility",
    "load_skill_reference",
    "list_documents",
    "get_document_info",
    "cross_document_analysis",
    "generate_report",
    "translate_text",
}


def _names(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}


def _schema_tokens(schemas: list[dict]) -> int:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(json.dumps(schemas, ensure_ascii=False)))


def _executor(cfg: AgentConfig) -> Executor:
    return Executor(MagicMock(), cfg, AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0))


def test_default_schema_budget_and_resident_set():
    """默认配置：常驻 10–12 个工具，schema ≤1,500 tokens。"""
    schemas = tool_registry.build_tool_schemas(AgentConfig())
    names = _names(schemas)

    assert names == DEFAULT_RESIDENT
    assert 10 <= len(schemas) <= 12
    tokens = _schema_tokens(schemas)
    assert tokens <= 1500, f"默认 schema {tokens} tokens 超出预算（≤1,500）"


def test_step1_gating_surface_budget():
    """第一步口径（仅门控 terminal/feishu/delegate，扩展面仍可见）≤3,500 tokens。"""
    from app.agent.config import DEFAULT_DISABLED_TOOLS

    first_step_gated = {
        "terminal_open",
        "terminal_send",
        "terminal_read",
        "terminal_signal",
        "terminal_close",
        "terminal_list",
        "feishu_bitable_query",
        "delegate_subtask",
    }
    schemas = [
        entry.to_openai_schema()
        for entry in tool_registry.list_tools()
        if entry.name not in DEFAULT_DISABLED_TOOLS and entry.name not in first_step_gated
    ]
    tokens = _schema_tokens(schemas)
    assert tokens <= 3500, f"第一步 schema {tokens} tokens 超出预算（≤3,500）"


def test_schema_only_emits_canonical_names():
    """schema 只发规范名：hidden 旧名与全部别名都不出现。"""
    schemas = tool_registry.build_tool_schemas(AgentConfig())
    names = _names(schemas)

    assert not (set(TOOL_ALIASES) & names), "别名不得出现在 schema 中"
    for spec in TOOL_ALIASES.values():
        assert spec.target in names or tool_registry.get(spec.target) is not None

    hidden = {t.name for t in tool_registry.list_tools(include_hidden=True) if t.hidden}
    assert hidden, "应存在 hidden 旧名（合并后保留注册）"
    assert not (hidden & names)


def test_consumers_share_single_source():
    """loop / executor / planner / /agent/tools 与 build_tool_schemas 同源。"""
    cfg = AgentConfig()
    expected = _names(tool_registry.build_tool_schemas(cfg))
    assert expected == DEFAULT_RESIDENT

    executor = _executor(cfg)
    step_tools = executor._get_step_tools(PlanStep(id="s1", description="x"))
    assert _names(step_tools or []) == expected

    loop = PERAgentLoop(MagicMock(), cfg, organization_id=1)
    assert _names(loop._get_tools() or []) == expected

    planner = Planner(MagicMock(), cfg)
    description = planner._build_tools_description()
    for name in expected:
        assert name in description
    assert "terminal_open" not in description
    assert "delegate_subtask" not in description

    from app.api.v1.endpoints.agent import list_tools as list_tools_endpoint

    payload = asyncio.run(list_tools_endpoint(current_user=MagicMock()))
    catalog_names = {item["name"] for item in payload["data"]}
    active_names = {item["name"] for item in payload["data"] if item["active_by_default"]}
    # 端点保留“工具目录”契约（非 hidden 全量），但活动面必须与注入面同源。
    assert catalog_names == {t.name for t in tool_registry.list_tools()}
    assert active_names == expected
    assert payload["active_by_default"] == len(expected)
    assert not (
        catalog_names & {t.name for t in tool_registry.list_tools(include_hidden=True) if t.hidden}
    )


def test_terminal_family_gated_by_setting(monkeypatch):
    """TERMINAL_ENABLED=false（默认）→ 六工具不注入；true → 整族注入。"""
    assert not (
        {"terminal_open", "terminal_send"} & _names(tool_registry.build_tool_schemas(AgentConfig()))
    )

    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    names = _names(tool_registry.build_tool_schemas(AgentConfig()))
    assert {
        "terminal_open",
        "terminal_send",
        "terminal_read",
        "terminal_signal",
        "terminal_close",
        "terminal_list",
    } <= names


def test_feishu_gated_by_credentials(monkeypatch):
    """飞书工具只在 FEISHU_APP_ID/SECRET 齐备时注入。"""
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    assert "feishu_bitable_query" not in _names(tool_registry.build_tool_schemas(AgentConfig()))

    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")
    assert "feishu_bitable_query" in _names(tool_registry.build_tool_schemas(AgentConfig()))


def test_delegation_gated_by_config():
    """delegate_subtask 默认关闭；enable_delegation=true 才注入。"""
    assert "delegate_subtask" not in _names(tool_registry.build_tool_schemas(AgentConfig()))
    assert "delegate_subtask" in _names(
        tool_registry.build_tool_schemas(AgentConfig(enable_delegation=True))
    )


def test_extended_surface_unlocked_by_explicit_tags():
    """扩展工具默认隐藏；显式 tool_tags（受限/操作者选择）解锁对应 tag。"""
    default_names = _names(tool_registry.build_tool_schemas(AgentConfig()))
    extended = {
        "extract_keywords",
        "compare_documents",
        "document_comparison",
        "knowledge_analysis",
        "deep_research",
        "list_conversations",
        "list_prompt_templates",
    }
    assert not (extended & default_names)

    tagged = _names(tool_registry.build_tool_schemas(AgentConfig(tool_tags=["analysis", "skill"])))
    assert {
        "extract_keywords",
        "compare_documents",
        "document_comparison",
        "knowledge_analysis",
        "deep_research",
    } <= tagged
    # tag 交集语义：不匹配显式 tag 的常驻工具不出现在该受限上下文。
    assert "search_knowledge_base" not in tagged
