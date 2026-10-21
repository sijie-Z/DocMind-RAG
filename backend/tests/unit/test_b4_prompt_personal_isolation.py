"""B4（评审 B4-04）：个人模板隔离 —— RAG 系统提示词选择 + Agent 提示词工具。

个人模板只影响自己：同组织其他成员（含组织管理员）既不能通过 RAG 系统提示词
命中它，也不能通过 Agent 的 list/get 工具看到它。
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.organization import Organization
from app.models.prompt import PromptTemplate
from app.services.chat_service import get_system_prompt


async def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


def _seed_templates() -> list[PromptTemplate]:
    # 同组织、同 category=rag、全部激活；id 顺序决定「最新」优先级。
    return [
        PromptTemplate(
            name="org-rag",
            content="ORG",
            category="rag",
            is_active=True,
            is_system=False,
            organization_id=1,
            scope="organization",
            creator_id=10,
            version=1,
        ),
        PromptTemplate(
            name="personal-a",
            content="PERSONAL-A",
            category="rag",
            is_active=True,
            is_system=False,
            organization_id=1,
            scope="personal",
            creator_id=1,
            version=1,
        ),
        PromptTemplate(
            name="personal-b",
            content="PERSONAL-B",
            category="rag",
            is_active=True,
            is_system=False,
            organization_id=1,
            scope="personal",
            creator_id=2,
            version=1,
        ),
    ]


async def test_get_system_prompt_isolates_personal_templates():
    engine = await _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            s.add(Organization(id=1, name="org-a", owner_id=10))
            s.add_all(_seed_templates())
            await s.commit()

        async with factory() as s:
            # 用户 1 命中自己的个人模板
            assert await get_system_prompt(s, False, organization_id=1, user_id=1) == "PERSONAL-A"
            # 用户 2 命中自己的个人模板
            assert await get_system_prompt(s, False, organization_id=1, user_id=2) == "PERSONAL-B"
            # 用户 3 没有个人模板 → 组织模板
            assert await get_system_prompt(s, False, organization_id=1, user_id=3) == "ORG"
            # 无 user_id（旧调用面）：绝不命中任何个人模板
            assert await get_system_prompt(s, False, organization_id=1) == "ORG"
    finally:
        await engine.dispose()


async def test_agent_prompt_tools_isolate_personal_templates(monkeypatch):
    import app.core.database as database_module
    from app.agent.core_tools import get_prompt_template, list_prompt_templates

    engine = await _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "AsyncSessionLocal", factory)
    try:
        async with factory() as s:
            s.add(Organization(id=1, name="org-a", owner_id=10))
            s.add_all(_seed_templates())
            await s.commit()

        listing = await list_prompt_templates(organization_id=1, user_id=1)
        assert "personal-a" in listing
        assert "personal-b" not in listing
        assert "org-rag" in listing

        own = await get_prompt_template("personal-a", organization_id=1, user_id=1)
        assert "PERSONAL-A" in own

        other = await get_prompt_template("personal-b", organization_id=1, user_id=1)
        assert "not found" in other

        other_owner = await get_prompt_template("personal-b", organization_id=1, user_id=2)
        assert "PERSONAL-B" in other_owner
    finally:
        await engine.dispose()
