"""issue #106 的接线回归测试。

#106 的形态是「资产造好没通电」——字段/分支/模块都存在，但没有任何调用点。
这里针对本次接上的三处各写一条，断言的是**行为**而不是"代码里有这行"。
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """独立 ExperienceStore：不读环境遗留的 benchmark/experiences.json
    （gitignored，上一次测试运行可能写过），不碰 Redis。

    round2 内容级去重落地（store.add 同内容不再新增）后必须的确定性前提：
    本用例断言的是"新经验 +1"，只有在空库上才成立——旧库存在同一模板
    经验时（那正是修复目标形态），重复写入本就不该增长。"""
    import app.agent.experience.store as store_mod

    monkeypatch.setattr("app.core.redis.redis_client", None)
    monkeypatch.setattr(store_mod, "LOCAL_PATH", tmp_path / "experiences.json")
    store_mod.reset_experience_store()
    yield store_mod.get_experience_store()
    store_mod.reset_experience_store()

# ── #106 §2.6：requires_auth 此前只 logger.debug，从不阻断 ────────────────

class TestRequiresAuthEnforced:
    """要求：声明了 requires_auth 的工具，在缺少调用者身份时必须被拦下。"""

    @pytest.mark.asyncio
    async def test_blocked_without_organization_context(self):
        from app.agent import core_tools  # noqa: F401  触发工具注册
        from app.agent.registry import tool_registry

        # 不传 organization_id —— 相当于调用方没有身份
        result = await tool_registry.execute_detailed("list_documents", {})

        assert not result.success, "缺少组织上下文时应拒绝执行，而不是放行"
        assert result.error is not None
        assert result.error.code == "auth_required", (
            f"应返回 auth_required，实际 {result.error.code}"
        )

    @pytest.mark.asyncio
    async def test_not_blocked_when_identity_present(self):
        """带上组织上下文时不应因 auth_required 被拦（后续可能因其他原因失败）。"""
        from app.agent import core_tools  # noqa: F401
        from app.agent.registry import tool_registry

        result = await tool_registry.execute_detailed(
            "list_documents", {}, organization_id=1, user_id=1
        )

        code = result.error.code if result.error else None
        assert code != "auth_required", "带了组织上下文却仍被 auth_required 拦住"

    @pytest.mark.asyncio
    async def test_tool_without_requires_auth_unaffected(self):
        """没声明 requires_auth 的工具不受影响 —— 确认拦截面没有扩大。"""
        from app.agent import core_tools  # noqa: F401
        from app.agent.registry import tool_registry

        entry = tool_registry.get("get_current_time")
        assert entry is not None and not entry.requires_auth

        result = await tool_registry.execute_detailed("get_current_time", {})
        code = result.error.code if result.error else None
        assert code != "auth_required"


# ── #106 §2.1：经验回路的写入侧此前完全没接 ─────────────────────────────

class TestExperienceWriteBack:
    """要求：runtime 真的会往 ExperienceStore 写，而不只是 planner 读。"""

    @pytest.mark.asyncio
    async def test_failed_step_writes_an_experience(self, isolated_store):
        from app.agent.config import AgentConfig
        from app.agent.exec_context import ExecutionContext
        from app.agent.loop import PERAgentLoop
        from app.agent.planner import Plan, PlanStep

        store = isolated_store
        before = await store.count()

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)
        ctx = ExecutionContext(query="查询营收数据")
        plan = Plan(id="p1", goal="查询营收数据", steps=[
            PlanStep(
                id="s1", description="调用外部 API",
                status="failed", error_context="connection refused",
            ),
        ])

        await loop._write_experiences(ctx, plan, None)

        after = await store.count()
        assert after > before, (
            "失败步骤应产生一条经验 —— 此前写入侧完全没接，经验库只能靠离线批量抽取增长"
        )

    @pytest.mark.asyncio
    async def test_successful_run_with_non_a_grades_writes_nothing(self, isolated_store):
        """质检不是全 A 时不记正向经验 —— 门槛刻意设高，避免污染 planner。"""
        from app.agent.config import AgentConfig
        from app.agent.exec_context import ExecutionContext
        from app.agent.loop import PERAgentLoop
        from app.agent.planner import Plan, PlanStep

        store = isolated_store
        before = await store.count()

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)
        ctx = ExecutionContext(query="q")
        plan = Plan(id="p1", goal="q", steps=[
            PlanStep(id="s1", description="d", status="completed", tool_hint="search_knowledge_base"),
        ])
        qg_result = {"grades": {"s1": ("C", "质量一般")}}

        await loop._write_experiences(ctx, plan, qg_result)

        assert await store.count() == before, "质检非全 A 时不应写入正向经验"
