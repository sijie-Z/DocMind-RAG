"""组织隔离判别性测试：技能与失败经验不得跨租户复用。

竞品对标 P0：此前 SkillManager 与 ExperienceStore 用全局 Redis key 和
进程级单例，A 组织学到的技能/失败教训会注入 B 组织的规划提示词。

变异点：
- 把 get_skill_manager 改成永远返回同一个实例 → 用例 1/2 红；
- 把 ExperienceStore.redis_key 改回全局 REDIS_KEY → 用例 3 红；
- 去掉 ctx.organization_id 的 checkpoint 往返 → 用例 4 红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.experience.store import ExperienceStore, get_experience_store
from app.agent.skills import SkillManager, get_skill_manager


class TestSkillTenantIsolation:
    def test_different_orgs_get_different_managers_and_keys(self):
        a = get_skill_manager(1)
        b = get_skill_manager(2)
        assert a is not b, "不同组织不得共用 SkillManager 实例"
        assert a.redis_key == "agent:skills:org:1"
        assert b.redis_key == "agent:skills:org:2"
        assert a.redis_key != b.redis_key

    @pytest.mark.asyncio
    async def test_skill_not_visible_to_other_org(self):
        a = SkillManager(organization_id=10)
        b = SkillManager(organization_id=20)

        fake_redis = MagicMock()
        store: dict[str, str] = {}

        async def get(key):
            return store.get(key)

        async def setex(key, ttl, value):
            store[key] = value

        fake_redis.get = AsyncMock(side_effect=get)
        fake_redis.setex = AsyncMock(side_effect=setex)

        with patch("app.core.redis.redis_client", fake_redis):
            await a.create_skill("org10-skill", "d", ["secret"], [{"tool_name": "t"}])
            await b.load()

        assert len(a.list_skills()) == 1
        assert b.list_skills() == [], "B 组织不得读到 A 组织学到的技能"


class TestExperienceTenantIsolation:
    def test_experience_stores_have_distinct_keys(self):
        a = ExperienceStore(organization_id=10)
        b = ExperienceStore(organization_id=20)
        assert a.redis_key != b.redis_key
        assert a.local_path != b.local_path

    def test_get_experience_store_scoped_by_org(self):
        assert get_experience_store(1) is not get_experience_store(2)


class TestContextOrgRoundTrip:
    def test_organization_id_survives_checkpoint_round_trip(self):
        from app.agent.exec_context import ExecutionContext

        ctx = ExecutionContext(query="q")
        ctx.organization_id = 42
        restored = ExecutionContext.from_checkpoint_dict(ctx.to_checkpoint_dict())
        assert restored.organization_id == 42

    def test_organization_id_not_in_public_to_dict(self):
        from app.agent.exec_context import ExecutionContext

        ctx = ExecutionContext(query="q")
        ctx.organization_id = 42
        assert "organization_id" not in ctx.to_dict(), "replay/SSE 既有键集契约不许改"


class TestTenantWiring:
    """接线判别：验证真实调用路径按组织取 store，而非只验工厂产物。"""

    @pytest.mark.asyncio
    async def test_loop_sets_ctx_organization_id(self):
        from unittest.mock import MagicMock

        from app.agent.config import AgentConfig
        from app.agent.loop import PERAgentLoop

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=77, user_id=1)
        # 批次十：Redis SkillManager 已移出生产 loop；租户字段仍必须按构造参数落账。
        assert loop.organization_id == 77
        assert not hasattr(loop, "skill_manager"), "生产 loop 不得再持有 Redis SkillManager"

    def test_get_experience_store_uses_org_for_redis_key(self):
        a = get_experience_store(101)
        b = get_experience_store(102)
        assert a.redis_key == "agent:experience:store:org:101"
        assert b.redis_key == "agent:experience:store:org:102"

    @pytest.mark.asyncio
    async def test_write_side_uses_organization_scoped_store(self):
        """loop._write_experiences 必须按 self.organization_id 取 store。"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from app.agent.config import AgentConfig
        from app.agent.loop import PERAgentLoop

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=88, user_id=1)
        fake_store = MagicMock()
        fake_store.add = AsyncMock()
        with patch("app.agent.experience.get_experience_store", return_value=fake_store) as getter:
            with patch(
                "app.agent.experience.extractor.extract_from_runtime_error",
                new=AsyncMock(return_value=None),
            ):
                await loop._write_experiences(
                    SimpleNamespace(query="q"),
                    SimpleNamespace(steps=[]),
                    {"grades": {"s": ("A", "")}},
                )
        getter.assert_called_once_with(88)
