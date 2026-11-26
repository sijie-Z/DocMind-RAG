"""批次十接线判别：生产路径只读 reviewed-only 组织技能库，Redis 旁路已关闭。

判别点：
- ``app/agent/loop.py`` / ``app/api/v1/endpoints/agent.py`` 不再 import/调用
  ``SkillManager`` / ``get_skill_manager``；全仓生产代码无 ``get_skill_manager``；
- loop 的运行时 skills 查询指向 ``org_skill_service.match_skill``：DB 中
  approved+enabled 技能会被注入；只预置 Redis ``agent:skills:org:<id>`` 不会；
- 把实现改回 ``SkillManager.match``（例如 monkeypatch 它抛异常）→ DB 注入用例红；
- 旧 ``GET /agent/skills`` 只读新库，不再触碰 Redis；
- ``_learn_skills`` 不再自动创建技能行。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.agent.tools  # noqa: F401
import app.models  # noqa: F401
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan, PlanStep
from app.core.database import Base
from app.models.org_skill import OrgSkill, OrgSkillVersion
from app.models.organization import Organization
from app.models.user import User
from app.services import org_skill_service as svc

ORG = 1
AUTHOR = 1
REVIEWER = 2
BACKEND_ROOT = Path(__file__).resolve().parents[2]

PAYLOAD = {
    "schema_version": 1,
    "trigger_phrases": ["总结季度财务报告"],
    "tool_sequence": [{"tool_name": "search_knowledge_base"}],
}


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'org-skill-wiring.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                User(id=AUTHOR, username="a", email="a@e.com", hashed_password="x"),
                User(id=REVIEWER, username="r", email="r@e.com", hashed_password="x"),
            ]
        )
        await db.flush()
        db.add(Organization(id=ORG, name="Org", owner_id=AUTHOR))
        await db.commit()
    yield maker
    await engine.dispose()


class _RedisSpy:
    def __init__(self, data: dict[str, str] | None = None):
        self.data = data or {}
        self.get_calls = 0
        self.setex_calls = 0

    async def get(self, key):
        self.get_calls += 1
        return self.data.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls += 1
        self.data[key] = value


async def _seed_enabled_skill(factory) -> str:
    async with factory() as db:
        skill, version = await svc.create_proposal(
            db,
            organization_id=ORG,
            user_id=AUTHOR,
            name="季度总结",
            payload=PAYLOAD,
        )
        await svc.review_version(
            db,
            organization_id=ORG,
            skill_id=skill.id,
            version_no=version.version_no,
            reviewer_id=REVIEWER,
            decision="approved",
        )
        await svc.enable_version(
            db,
            organization_id=ORG,
            skill_id=skill.id,
            version_no=version.version_no,
            user_id=REVIEWER,
        )
        return skill.id


def test_production_has_no_skill_manager_imports():
    loop_src = (BACKEND_ROOT / "app" / "agent" / "loop.py").read_text(encoding="utf-8")
    agent_api_src = (BACKEND_ROOT / "app" / "api" / "v1" / "endpoints" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert "app.agent.skills" not in loop_src
    assert "from app.agent.skills import" not in loop_src
    assert "get_skill_manager" not in loop_src
    assert "skill_manager." not in loop_src, "loop 不得再调用任何 SkillManager 方法"
    assert "get_skill_manager" not in agent_api_src
    assert "app.agent.skills" not in agent_api_src

    offenders = []
    skills_module = BACKEND_ROOT / "app" / "agent" / "skills.py"
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        if path == skills_module:
            continue
        text = path.read_text(encoding="utf-8")
        if "get_skill_manager" in text or "from app.agent.skills import" in text:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert not offenders, f"生产代码仍引用 Redis SkillManager: {offenders}"


@pytest.mark.asyncio
async def test_reviewed_db_skill_is_injected_and_redis_ignored(factory, monkeypatch):
    """DB 中 approved+enabled 技能注入；预置 Redis 不参与匹配。"""
    from app.agent import loop as loop_module
    from app.agent.loop import PERAgentLoop

    await _seed_enabled_skill(factory)
    monkeypatch.setattr(loop_module, "AsyncSessionLocal", factory)
    redis_spy = _RedisSpy(
        {
            f"agent:skills:org:{ORG}": (
                '[{"id":"redis-skill","name":"Redis","description":"d",'
                '"trigger_patterns":["总结季度财务报告"],'
                '"tool_sequence":[{"tool_name":"web_search"}],'
                '"success_count":9,"failure_count":0,"created_at":0,"last_used_at":9999999999}]'
            )
        }
    )
    monkeypatch.setattr("app.core.redis.redis_client", redis_spy)

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=ORG)
    assert not hasattr(loop, "skill_manager")
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])

    matched_id = await loop._apply_skill_hints(plan, "帮我总结季度财务报告")
    assert matched_id is not None
    assert plan.steps[0].tool_hint == "search_knowledge_base", "应注入 DB reviewed 技能"
    assert redis_spy.get_calls == 0, "生产路径不得读取 Redis 技能键"


@pytest.mark.asyncio
async def test_loop_passes_visible_tool_names_to_matcher(monkeypatch):
    """loop 必须把当前可见工具集传给 matcher；删掉该参数 → 本用例红。"""
    from app.agent.loop import PERAgentLoop

    captured: dict = {}

    async def _match(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            skill_id="s1",
            version_no=1,
            tool_sequence=[{"tool_name": "search_knowledge_base"}],
        )

    monkeypatch.setattr(svc, "match_skill", _match)
    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=ORG)
    monkeypatch.setattr(
        loop,
        "_get_tools",
        lambda: [{"function": {"name": "search_knowledge_base"}}],
    )
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])
    await loop._apply_skill_hints(plan, "总结季度财务报告")
    assert captured.get("visible_tool_names") == {"search_knowledge_base"}


@pytest.mark.asyncio
async def test_redis_only_skill_is_not_injected(factory, monkeypatch):
    from app.agent import loop as loop_module
    from app.agent.loop import PERAgentLoop

    monkeypatch.setattr(loop_module, "AsyncSessionLocal", factory)
    redis_spy = _RedisSpy(
        {
            f"agent:skills:org:{ORG}": (
                '[{"id":"redis-skill","name":"Redis","description":"d",'
                '"trigger_patterns":["总结季度财务报告"],'
                '"tool_sequence":[{"tool_name":"web_search"}],'
                '"success_count":99,"failure_count":0,"created_at":0,"last_used_at":9999999999}]'
            )
        }
    )
    monkeypatch.setattr("app.core.redis.redis_client", redis_spy)

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=ORG)
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])
    assert await loop._apply_skill_hints(plan, "帮我总结季度财务报告") is None
    assert plan.steps[0].tool_hint is None, "未审核的 Redis 技能不得注入"
    assert redis_spy.get_calls == 0


@pytest.mark.asyncio
async def test_reverting_to_skill_manager_match_breaks_db_injection(factory, monkeypatch):
    """变异判别：SkillManager.match 被改成抛异常也不影响 reviewed 路径。

    若实现退回 ``SkillManager.match``，本用例会因异常/未注入而红。
    """
    from app.agent import loop as loop_module
    from app.agent.loop import PERAgentLoop
    from app.agent.skills import SkillManager

    await _seed_enabled_skill(factory)
    monkeypatch.setattr(loop_module, "AsyncSessionLocal", factory)

    def _boom(self, query):
        raise AssertionError("生产路径不得调用 SkillManager.match")

    monkeypatch.setattr(SkillManager, "match", _boom)
    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=ORG)
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])
    matched_id = await loop._apply_skill_hints(plan, "帮我总结季度财务报告")
    assert matched_id == await _only_skill_id(factory)
    assert plan.steps[0].tool_hint == "search_knowledge_base"


async def _only_skill_id(factory) -> str:
    async with factory() as db:
        return (await db.execute(select(OrgSkill.id))).scalar_one()


@pytest.mark.asyncio
async def test_legacy_agent_skills_endpoint_reads_new_library(factory, monkeypatch):
    from app.api.v1.endpoints.agent import list_skills

    skill_id = await _seed_enabled_skill(factory)
    redis_spy = _RedisSpy({f"agent:skills:org:{ORG}": "[]"})
    monkeypatch.setattr("app.core.redis.redis_client", redis_spy)
    user = SimpleNamespace(
        id=AUTHOR,
        username="a",
        is_active=True,
        is_superuser=False,
        organization_id=ORG,
        role="user",
        api_key_scopes=None,
    )
    async with factory() as db:
        result = await list_skills(current_user=user, db=db)
    assert [row["id"] for row in result["data"]] == [skill_id]
    assert result["data"][0]["success_rate"] == 0.0
    assert redis_spy.get_calls == 0, "旧端点不得再读 Redis"


@pytest.mark.asyncio
async def test_learn_skills_creates_no_rows(factory, monkeypatch):
    from app.agent import loop as loop_module
    from app.agent.loop import PERAgentLoop

    monkeypatch.setattr(loop_module, "AsyncSessionLocal", factory)
    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=ORG)
    plan = Plan(
        id="p1",
        goal="g",
        steps=[
            PlanStep(id="s1", description="a", tool_hint="search_knowledge_base"),
            PlanStep(id="s2", description="b", tool_hint="web_search"),
        ],
    )
    ctx = ExecutionContext(query="总结季度财务报告")
    await loop._learn_skills(ctx, plan, {"grades": {"s1": ("A", ""), "s2": ("A", "")}})
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(OrgSkill))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(OrgSkillVersion))).scalar() == 0


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}

    def reset_working(self):
        self.results = {}

    def get_all_results(self):
        return dict(self.results)

    def store_step_result(self, step_id, result):
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_interaction(self, user_input, assistant_response):
        pass


class _FakePlanner:
    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(
            type="plan_step",
            plan_id="plan-1",
            plan_step_id="s1",
            content="唯一步骤",
            tool_hint=None,
        )
        yield AgentEvent(
            type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成"
        )


class _FakeExecutor:
    def __init__(self, memory):
        self.memory = memory
        self.seen_tool_hints: list = []

    def reset_for_request(self):
        pass

    async def execute(
        self,
        plan,
        history=None,
        organization_id=1,
        user_id=0,
        enable_thinking=True,
        ctx=None,
        on_step_done=None,
        human_guidance="",
        document_context="",
    ):
        for step in plan.steps:
            self.seen_tool_hints.append(step.tool_hint)
            step.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(
                step.id,
                {
                    "description": step.description,
                    "status": "completed",
                    "result": "这是一段足够完整的执行结果内容，包含具体事实与结论。" * 5,
                    "tool_hint": step.tool_hint,
                },
            )
        yield AgentEvent(type="chunk", content="答案")


class _FakeReflector:
    async def reflect(self, plan, results, ctx=None):
        yield AgentEvent(type="reflection", reflection_result="pass", content="", plan_id=plan.id)


class _FakeReviewer:
    async def review(self, plan, results, ctx=None):
        return {"issues_found": [], "recommendation": "pass"}


@pytest.mark.asyncio
async def test_full_run_injects_reviewed_skill(factory, monkeypatch):
    """run 级：预置 Redis 后运行，只注入 DB reviewed 技能。"""
    from app.agent import loop as loop_module
    from app.agent.loop import PERAgentLoop

    await _seed_enabled_skill(factory)
    monkeypatch.setattr(loop_module, "AsyncSessionLocal", factory)
    redis_spy = _RedisSpy(
        {
            f"agent:skills:org:{ORG}": (
                '[{"id":"redis-skill","name":"Redis","description":"d",'
                '"trigger_patterns":["帮我总结季度财务报告"],'
                '"tool_sequence":[{"tool_name":"web_search"}],'
                '"success_count":99,"failure_count":0,"created_at":0,"last_used_at":9999999999}]'
            )
        }
    )
    monkeypatch.setattr("app.core.redis.redis_client", redis_spy)
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock(return_value=False))

    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False),
        organization_id=ORG,
        user_id=AUTHOR,
    )
    memory = _FakeMemory()
    loop.memory = memory
    loop._planner = _FakePlanner()
    loop._executor = _FakeExecutor(memory)
    loop._reflector = _FakeReflector()
    loop._reviewer = _FakeReviewer()

    events = await asyncio.wait_for(_collect(loop), timeout=15.0)
    assert loop._executor.seen_tool_hints == ["search_knowledge_base"]
    assert any(e.type == "done" for e in events)
    assert redis_spy.get_calls == 0


async def _collect(loop) -> list:
    return [event async for event in loop.run("帮我总结季度财务报告")]
