"""A2 评审收口：提示词的两个非端点消费方也必须按组织过滤。

B-1：Agent 工具 ``list_prompt_templates`` / ``get_prompt_template`` 默认可被
任意登录用户的 Agent 调用——A2 之后提示词是组织私有数据，这两个工具必须
同源过滤，否则可枚举/读取其他组织的私有模板全文。
B-2：``chat_service.get_system_prompt`` 此前全库取最新激活 rag 模板，任意用户
可跨租户注入系统提示词；现在只允许本组织 + 系统内置模板。

真 SQLite；工具内部使用 ``AsyncSessionLocal``，测试以 monkeypatch 注入同库会话。
"""
import json
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.organization import Organization
from app.models.prompt import PromptTemplate
from app.models.user import User


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            Organization(id=1, name="OrgA", owner_id=1),
            Organization(id=2, name="OrgB", owner_id=3),
            User(id=1, username="u1", email="u1@example.com", hashed_password="x",
                 role="admin", organization_id=1, is_active=True),
        ])
        await s.flush()
        s.add_all([
            PromptTemplate(
                name="org1-secret", content="TOP SECRET org1 prompt",
                is_system=False, organization_id=1, creator_id=1,
                category="rag", is_active=True, updated_at=datetime(2025, 5, 1),
            ),
            PromptTemplate(
                name="org2-rag", content="org2 rag prompt",
                is_system=False, organization_id=2, creator_id=3,
                category="rag", is_active=True, updated_at=datetime(2025, 4, 1),
            ),
            PromptTemplate(
                name="sys-rag", content="system rag prompt",
                is_system=True, organization_id=None,
                category="rag", is_active=True, updated_at=datetime(2025, 3, 1),
            ),
            PromptTemplate(
                name="legacy-orphan", content="orphan content",
                is_system=False, organization_id=None, creator_id=None,
                category="rag", is_active=True, updated_at=datetime(2025, 6, 1),
            ),
        ])
        await s.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_agent_prompt_tools_filter_by_org():
    from app.agent.core_tools import get_prompt_template, list_prompt_templates

    engine, factory = await _setup()
    try:
        with patch("app.core.database.AsyncSessionLocal", new=factory):
            listing = await list_prompt_templates(organization_id=2)
            assert "org2-rag" in listing
            assert "sys-rag" in listing
            assert "org1-secret" not in listing, "不得枚举其他组织的私有模板"
            assert "legacy-orphan" not in listing, "非系统 NULL 孤儿行不得到处可见"

            foreign = await get_prompt_template("org1-secret", organization_id=2)
            assert "not found" in foreign
            assert "TOP SECRET" not in foreign, "不得读取其他组织私有模板全文"

            system = await get_prompt_template("sys-rag", organization_id=2)
            assert "system rag prompt" in json.loads(system)["content"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_prompt_tools_fail_closed_without_org():
    from app.agent.core_tools import get_prompt_template, list_prompt_templates
    from app.agent.registry import ToolResult

    engine, factory = await _setup()
    try:
        with patch("app.core.database.AsyncSessionLocal", new=factory):
            for result in (
                await list_prompt_templates(organization_id=None),
                await get_prompt_template("sys-rag", organization_id=None),
            ):
                assert isinstance(result, ToolResult)
                assert result.success is False
                assert result.error.code == "organization_required"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rag_system_prompt_is_org_scoped():
    from app.services.chat_service import get_system_prompt

    engine, factory = await _setup()
    try:
        async with factory() as s:
            # org 2：只看得到 org2 + 系统模板；org1 的（更新更晚）不得胜出
            assert await get_system_prompt(s, False, organization_id=2) == "org2 rag prompt"
            # 无组织：只允许系统内置模板
            assert await get_system_prompt(s, False, organization_id=None) == "system rag prompt"
            # org 1：自己的模板（latest）生效
            assert await get_system_prompt(s, False, organization_id=1) == "TOP SECRET org1 prompt"
    finally:
        await engine.dispose()
