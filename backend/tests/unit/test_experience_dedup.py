"""经验库同质膨胀修复（round2 后端卫生①）—— 判别用例。

实锤形态：runtime 每轮质检全 A 都会调 extract_from_runtime_success
（loop.py:1159 一带）、失败步骤调 extract_from_runtime_error；两个抽取器
**每次调用都铸造新 uuid**（Experience.id default_factory），而 store.add
只按 id 去重 → 同一模板经验被无限重复累加。现实库
benchmark/experiences.json 47 条中 19 条逐字相同（tool_reliability /
API 连接失败），另有 23 条仅查询前缀不同的同模板 successful_pattern。
另有开关不一致：写侧 _write_experiences 不受 enable_experience 门控
（agent_api.py 显式 =False 的路径仍写库），读侧 planner 却受控。

修复（写入侧两条腿）：
1. store.add 内容级去重：Experience.content_key() 归一化 (scenario,
   symptom, lesson)；同内容不同 id → 视为同一条经验，已存记录胜出
   （保住累积 confidence/verified_count），新记录不落库；同 id 再 add
   仍原地更新。
2. _write_experiences 尊重 enable_experience（写读同门控）。

变异点：
- 去掉 store.add 的内容去重（退回纯 id 覆盖）→
  test_same_content_different_id_added_once / test_same_runtime_success_*
  精确红；
- 去掉 _write_experiences 的 enable_experience 门控 →
  test_disabled_experience_writes_nothing 精确红。
"""

from unittest.mock import MagicMock

import pytest

from app.agent.experience.models import Experience


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """独立 ExperienceStore：不碰 Redis、本地 JSON 落 tmp_path。"""
    import app.agent.experience.store as store_mod

    # store 在方法内 `from app.core.redis import redis_client` → 按模块属性
    # 查找；置 None 使其走"无 Redis"分支（不依赖本机是否有 Redis 在跑）。
    monkeypatch.setattr("app.core.redis.redis_client", None)
    monkeypatch.setattr(store_mod, "LOCAL_PATH", tmp_path / "experiences.json")
    store_mod.reset_experience_store()
    yield store_mod.get_experience_store()
    store_mod.reset_experience_store()


def _success_plan():
    from app.agent.planner import Plan, PlanStep

    return Plan(id="p1", goal="查询营收数据", steps=[
        PlanStep(id="s1", description="检索", status="completed",
                 tool_hint="search_knowledge_base"),
    ])


def _loop(enable_experience: bool):
    from app.agent.config import AgentConfig
    from app.agent.loop import PERAgentLoop

    config = AgentConfig(enable_experience=enable_experience)
    return PERAgentLoop(MagicMock(), config, organization_id=1, user_id=0)


class TestStoreContentDedup:
    """store.add：同内容（归一化后）不同 id 不再重复落库。"""

    @pytest.mark.asyncio
    async def test_same_content_different_id_added_once(self, isolated_store):
        store = isolated_store
        for _ in range(3):
            await store.add(Experience(
                scenario="tool_reliability",
                symptom="api_connection_failure",
                lesson="API 连接失败时，自动启用指数退避重试（最多 3 次），并降级到备用工具。",
            ))
        assert await store.count() == 1, (
            "同一条经验抽三次应按内容去重为 1 条（随机 id 不构成身份）"
        )

    @pytest.mark.asyncio
    async def test_whitespace_and_case_normalized(self, isolated_store):
        store = isolated_store
        await store.add(Experience(scenario="Frame", symptom="s", lesson="keep   lesson\n clean"))
        await store.add(Experience(scenario="frame", symptom="S", lesson="  keep lesson clean  "))
        assert await store.count() == 1, "纯格式差异（空白/大小写）不应产生新条目"

    @pytest.mark.asyncio
    async def test_different_content_both_kept(self, isolated_store):
        store = isolated_store
        await store.add(Experience(scenario="a", symptom="s", lesson="lesson one"))
        await store.add(Experience(scenario="a", symptom="s", lesson="lesson two"))
        assert await store.count() == 2, "内容不同是真新经验，不得误合"

    @pytest.mark.asyncio
    async def test_same_id_still_updates_in_place(self, isolated_store):
        store = isolated_store
        exp = Experience(scenario="a", symptom="s", lesson="lesson", confidence=0.5)
        await store.add(exp)
        updated = Experience(
            id=exp.id, scenario="a", symptom="s", lesson="lesson", confidence=0.9,
        )
        await store.add(updated)
        stored = await store.get(exp.id)
        assert stored is updated
        assert await store.count() == 1, (
            "同 id 的 add 必须保持原地更新语义（不被内容去重逻辑误伤）"
        )


class TestWriteSideGate:
    """_write_experiences 尊重 enable_experience（写读同门控）。"""

    @pytest.mark.asyncio
    async def test_same_runtime_success_twice_does_not_grow(self, isolated_store):
        from app.agent.exec_context import ExecutionContext

        store = isolated_store
        loop = _loop(enable_experience=True)
        ctx = ExecutionContext(query="查询营收数据")
        qg = {"grades": {"s1": ("A", "quality ok")}}

        await loop._write_experiences(ctx, _success_plan(), qg)
        first = await store.count()
        assert first == 1, "前置：首次全 A 运行应真的落一条正向经验"
        await loop._write_experiences(ctx, _success_plan(), qg)
        assert await store.count() == first, "同一 runtime success 抽两次 → 库里不新增"

    @pytest.mark.asyncio
    async def test_repeated_runtime_error_lesson_not_duplicated(self, isolated_store):
        from app.agent.experience.extractor import extract_from_runtime_error

        store = isolated_store
        for _ in range(3):
            exp = await extract_from_runtime_error("API connection refused", query="q")
            assert exp is not None
            await store.add(exp)
        assert await store.count() == 1, (
            "同一错误模板三次抽取应去重为 1 条（现实库 19 条逐字重复的形态）"
        )

    @pytest.mark.asyncio
    async def test_disabled_experience_writes_nothing(self, isolated_store):
        from app.agent.exec_context import ExecutionContext
        from app.agent.planner import Plan, PlanStep

        store = isolated_store
        loop = _loop(enable_experience=False)
        ctx = ExecutionContext(query="查询营收数据")

        # 成功腿（全 A）
        await loop._write_experiences(ctx, _success_plan(), {"grades": {"s1": ("A", "ok")}})
        # 失败腿（可识别错误模板）
        failed_plan = Plan(id="p2", goal="查询营收数据", steps=[
            PlanStep(id="s2", description="调用外部 API", status="failed",
                     error_context="connection refused"),
        ])
        await loop._write_experiences(ctx, failed_plan, None)

        assert await store.count() == 0, (
            "enable_experience=False 时写侧必须整体关闭"
            "（agent_api.py 显式关闭的路径不得写库）"
        )
