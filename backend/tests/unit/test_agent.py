# -*- coding: utf-8 -*-
"""Agent 模块测试 —— Tool Registry / Config / Planner / Executor / Reflector / MemoryBridge."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, List


# ── Tool Registry ──────────────────────────────────────────────────────────

class TestToolRegistry:
    """ToolRegistry: 注册、查询、导出、执行."""

    def test_register_and_get(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def dummy_handler(query: str, **ctx) -> str:
            return f"result: {query}"

        registry.register(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=dummy_handler,
            tags=["test"],
        )
        entry = registry.get("test_tool")
        assert entry is not None
        assert entry.name == "test_tool"
        assert "test" in entry.tags

    def test_get_unknown_tool_returns_none(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_list_tools_with_tags(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def h1(**ctx): return "a"
        async def h2(**ctx): return "b"
        registry.register("tool_a", "desc", {"type": "object", "properties": {}}, h1, tags=["search"])
        registry.register("tool_b", "desc", {"type": "object", "properties": {}}, h2, tags=["analysis"])

        all_tools = registry.list_tools()
        assert len(all_tools) == 2

        search_tools = registry.list_tools(tags=["search"])
        assert len(search_tools) == 1
        assert search_tools[0].name == "tool_a"

    def test_to_openai_tools_format(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def h(**ctx): return "ok"
        registry.register("my_tool", "My tool description",
                         {"type": "object", "properties": {"x": {"type": "string"}}}, h)

        tools = registry.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "my_tool"
        assert tools[0]["function"]["description"] == "My tool description"
        assert "parameters" in tools[0]["function"]

    @pytest.mark.asyncio
    async def test_execute_success(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def greet(name: str, **ctx) -> str:
            return f"Hello, {name}!"

        registry.register("greet", "Greet someone",
                         {"type": "object", "properties": {"name": {"type": "string"}},
                          "required": ["name"]}, greet)

        result = await registry.execute("greet", {"name": "World"})
        assert result == "Hello, World!"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool_returns_error(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()
        result = await registry.execute("nope", {})
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_execute_handler_exception_returns_error(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def broken(**ctx) -> str:
            raise ValueError("something broke")

        registry.register("broken", "Broken tool",
                         {"type": "object", "properties": {}}, broken)
        result = await registry.execute("broken", {})
        assert "Error" in result
        assert "ValueError" in result

    def test_register_twice_overwrites(self):
        from app.agent.registry import ToolRegistry
        registry = ToolRegistry()

        async def h1(**ctx): return "v1"
        async def h2(**ctx): return "v2"

        registry.register("dup", "v1", {"type": "object", "properties": {}}, h1)
        registry.register("dup", "v2", {"type": "object", "properties": {}}, h2)
        assert registry.get("dup").description == "v2"


# ── AgentConfig ────────────────────────────────────────────────────────────

class TestAgentConfig:
    """AgentConfig: 序列化、反序列化、Redis 持久化."""

    def test_default_config(self):
        from app.agent.config import AgentConfig
        cfg = AgentConfig()
        assert cfg.model == "deepseek-v4-flash"
        assert cfg.temperature == 0.1
        assert cfg.enable_planning is True
        assert cfg.enable_reflection is True
        assert cfg.enable_tools is True
        assert cfg.max_plan_steps == 10
        assert cfg.max_retries_per_step == 3

    def test_to_dict_and_from_dict(self):
        from app.agent.config import AgentConfig
        cfg = AgentConfig(model="gpt-4", temperature=0.5, enable_reflection=False)
        data = cfg.to_dict()
        assert data["model"] == "gpt-4"
        assert data["temperature"] == 0.5
        assert data["enable_reflection"] is False

        restored = AgentConfig.from_dict(data)
        assert restored.model == "gpt-4"
        assert restored.temperature == 0.5
        assert restored.enable_reflection is False

    def test_from_dict_ignores_unknown_fields(self):
        from app.agent.config import AgentConfig
        data = {"model": "deepseek-v4-flash", "enable_planning": True, "unknown_field": "should_ignore"}
        cfg = AgentConfig.from_dict(data)
        assert cfg.model == "deepseek-v4-flash"
        assert not hasattr(cfg, "unknown_field")

    @pytest.mark.asyncio
    async def test_save_and_load_from_redis(self):
        """Test Redis persistence with real Redis (running in Docker)."""
        from app.agent.config import AgentConfig
        from app.core.redis import redis_client
        if not redis_client:
            pytest.skip("Redis not available")

        cfg = AgentConfig(model="deepseek-v4-flash", temperature=0.5)
        await cfg.save_to_redis("test-session-123", ttl=60)

        loaded = await AgentConfig.load_from_redis("test-session-123")
        assert loaded is not None
        assert loaded.model == "deepseek-v4-flash"
        assert loaded.temperature == 0.5

        # Cleanup
        await redis_client.delete("agent:config:test-session-123")

    @pytest.mark.asyncio
    async def test_load_from_redis_nonexistent(self):
        from app.agent.config import AgentConfig
        cfg = await AgentConfig.load_from_redis("no-such-session-test")
        assert cfg is None


# ── Agent Events ───────────────────────────────────────────────────────────

class TestAgentEvents:
    """AgentEvent 创建与类型."""

    def test_thinking_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(type="thinking", content="处理中...")
        assert event.type == "thinking"
        assert event.content == "处理中..."

    def test_tool_call_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(
            type="tool_call",
            tool_name="search_knowledge_base",
            tool_args={"query": "test"},
            tool_call_id="call_123",
        )
        assert event.type == "tool_call"
        assert event.tool_name == "search_knowledge_base"
        assert event.tool_args["query"] == "test"

    def test_tool_result_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(
            type="tool_result",
            tool_name="search_knowledge_base",
            content="result data",
            tool_duration_ms=150.0,
        )
        assert event.type == "tool_result"
        assert event.tool_duration_ms == 150.0

    def test_tool_error_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(type="tool_error", content="Connection timeout")
        assert event.type == "tool_error"
        assert "timeout" in event.content

    def test_plan_events(self):
        from app.agent.events import AgentEvent
        start = AgentEvent(type="plan_start", plan_id="p1", content="planning...")
        step = AgentEvent(type="plan_step", plan_id="p1", plan_step_id="s1",
                         content="Step 1", dependencies=[], tool_hint="search")
        complete = AgentEvent(type="plan_complete", plan_id="p1", content="done")

        assert start.type == "plan_start"
        assert step.plan_step_id == "s1"
        assert step.dependencies == []
        assert complete.type == "plan_complete"

    def test_reflection_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(type="reflection", reflection_result="pass", content="All good")
        assert event.type == "reflection"
        assert event.reflection_result == "pass"

    def test_done_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(type="done", plan_progress=1.0)
        assert event.type == "done"
        assert event.plan_progress == 1.0

    def test_error_event(self):
        from app.agent.events import AgentEvent
        event = AgentEvent(type="error", content="LLM API error")
        assert event.type == "error"


# ── Planner ────────────────────────────────────────────────────────────────

class TestPlanner:
    """Planner: JSON 提取、DAG 校验、Plan 结构."""

    def test_extract_json_from_code_fence(self):
        from app.agent.planner import Planner
        from app.agent.config import AgentConfig
        planner = Planner(None, AgentConfig())

        text = """一些推理过程
```json
{"goal": "test", "reasoning": "simple", "steps": [{"id": "s1", "description": "do something", "dependencies": []}]}
```"""
        result = planner._extract_json(text)
        assert result["goal"] == "test"
        assert len(result["steps"]) == 1

    def test_extract_json_raw(self):
        planner = self._make_planner()
        text = '{"goal": "raw", "steps": [{"id": "s1", "description": "x", "dependencies": []}]}'
        result = planner._extract_json(text)
        assert result["goal"] == "raw"

    def test_extract_json_braces(self):
        planner = self._make_planner()
        text = "some text\n{\"goal\": \"braces\", \"steps\": [{\"id\": \"s1\", \"description\": \"x\", \"dependencies\": []}]}\nmore text"
        result = planner._extract_json(text)
        assert result["goal"] == "braces"

    def test_extract_json_invalid_returns_empty(self):
        planner = self._make_planner()
        result = planner._extract_json("not json at all")
        assert result == {}

    def _make_planner(self):
        from app.agent.planner import Planner
        from app.agent.config import AgentConfig
        return Planner(None, AgentConfig())

    def test_parse_plan_valid(self):
        from app.agent.planner import Planner
        from app.agent.config import AgentConfig
        planner = Planner(None, AgentConfig())

        data = {
            "goal": "对比两份合同",
            "reasoning": "需要先搜索再对比",
            "steps": [
                {"id": "s1", "description": "搜索合同A", "dependencies": []},
                {"id": "s2", "description": "搜索合同B", "dependencies": []},
                {"id": "s3", "description": "对比差异", "dependencies": ["s1", "s2"]},
            ]
        }
        plan = planner._parse_plan("p123", data)
        assert plan is not None
        assert plan.goal == "对比两份合同"
        assert len(plan.steps) == 3
        assert plan.steps[2].dependencies == ["s1", "s2"]

    def test_parse_plan_empty_steps_returns_none(self):
        planner = self._make_planner()
        assert planner._parse_plan("p1", {"goal": "x", "steps": []}) is None

    def test_validate_dag_no_cycles(self):
        from app.agent.planner import Plan, PlanStep
        from app.agent.config import AgentConfig
        from app.agent.planner import Planner
        planner = Planner(None, AgentConfig())

        plan = Plan(
            id="p1",
            goal="test",
            steps=[
                PlanStep(id="s1", description="step 1", dependencies=[]),
                PlanStep(id="s2", description="step 2", dependencies=["s1"]),
                PlanStep(id="s3", description="step 3", dependencies=["s2"]),
            ]
        )
        # Should not raise
        planner._validate_dag(plan)
        assert plan.steps[0].dependencies == []

    def test_validate_dag_with_cycle_removes_deps(self):
        from app.agent.planner import Plan, PlanStep
        from app.agent.config import AgentConfig
        from app.agent.planner import Planner
        planner = Planner(None, AgentConfig())

        plan = Plan(
            id="p1",
            goal="cyclic",
            steps=[
                PlanStep(id="s1", description="step 1", dependencies=["s3"]),
                PlanStep(id="s2", description="step 2", dependencies=["s1"]),
                PlanStep(id="s3", description="step 3", dependencies=["s2"]),
            ]
        )
        planner._validate_dag(plan)
        # Cycle detected — all deps should be cleared
        assert all(s.dependencies == [] for s in plan.steps)

    def test_validate_dag_removes_invalid_refs(self):
        from app.agent.planner import Plan, PlanStep
        from app.agent.config import AgentConfig
        from app.agent.planner import Planner
        planner = Planner(None, AgentConfig())

        plan = Plan(
            id="p1",
            goal="invalid refs",
            steps=[
                PlanStep(id="s1", description="step 1", dependencies=["nonexistent"]),
                PlanStep(id="s2", description="step 2", dependencies=[]),
            ]
        )
        planner._validate_dag(plan)
        assert plan.steps[0].dependencies == []

    def test_plan_get_ready_steps(self):
        from app.agent.planner import Plan, PlanStep
        plan = Plan(
            id="p1",
            goal="test",
            steps=[
                PlanStep(id="s1", description="step 1", dependencies=[]),
                PlanStep(id="s2", description="step 2", dependencies=["s1"]),
                PlanStep(id="s3", description="step 3", dependencies=["s1"]),
                PlanStep(id="s4", description="step 4", dependencies=["s2", "s3"]),
            ]
        )
        ready = plan.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].id == "s1"

    def test_plan_progress(self):
        from app.agent.planner import Plan, PlanStep
        plan = Plan(id="p1", goal="test", completed_steps=1, steps=[
            PlanStep(id="s1", description="s1", status="completed"),
            PlanStep(id="s2", description="s2", status="pending"),
        ])
        assert plan.progress == 0.5
        assert not plan.is_complete

    def test_plan_is_complete(self):
        from app.agent.planner import Plan, PlanStep
        plan = Plan(id="p1", goal="test", completed_steps=2, steps=[
            PlanStep(id="s1", description="s1", status="completed"),
            PlanStep(id="s2", description="s2", status="skipped"),
        ])
        assert plan.is_complete
        assert plan.progress == 1.0


# ── Executor ───────────────────────────────────────────────────────────────

class TestExecutor:
    """Executor: 步骤执行、重试."""

    @pytest.mark.asyncio
    async def test_execute_step_once_llm_error_returns_tool_error(self):
        """LLM 调用失败时返回 tool_error."""
        from app.agent.executor import Executor
        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.agent.events import AgentEvent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API timeout"))

        memory = AgentMemoryBridge(agent_id="test", organization_id=1, user_id=0)
        executor = Executor(mock_client, AgentConfig(), memory)

        step = PlanStep(id="s1", description="test step")
        plan = Plan(id="p1", goal="test", steps=[step])

        events = []
        async for event in executor._execute_step_once(
            step=step, plan=plan, history=None,
            organization_id=1, user_id=0, enable_thinking=True,
        ):
            events.append(event)

        assert any(e.type == "tool_error" for e in events)

    def test_get_previous_results_empty(self):
        from app.agent.executor import Executor
        from app.agent.config import AgentConfig
        from app.agent.memory_bridge import AgentMemoryBridge
        mock_client = MagicMock()
        memory = AgentMemoryBridge(agent_id="test", organization_id=1, user_id=0)
        executor = Executor(mock_client, AgentConfig(), memory)
        assert executor._get_previous_results([]) == ""

    def test_get_step_tools_filters_disabled(self):
        from app.agent.executor import Executor
        from app.agent.config import AgentConfig
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.agent.planner import PlanStep
        mock_client = MagicMock()
        memory = AgentMemoryBridge(agent_id="test", organization_id=1, user_id=0)
        cfg = AgentConfig(disabled_tools=["execute_python", "execute_sql"])
        executor = Executor(mock_client, cfg, memory)
        step = PlanStep(id="s1", description="test")
        tools = executor._get_step_tools(step)
        if tools:
            names = [t.get("function", {}).get("name") for t in tools]
            assert "execute_python" not in names
            assert "execute_sql" not in names

    def test_get_step_tools_prioritizes_hint(self):
        from app.agent.executor import Executor
        from app.agent.config import AgentConfig
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.agent.planner import PlanStep
        mock_client = MagicMock()
        memory = AgentMemoryBridge(agent_id="test", organization_id=1, user_id=0)
        executor = Executor(mock_client, AgentConfig(), memory)
        step = PlanStep(id="s1", description="test", tool_hint="search_knowledge_base")
        tools = executor._get_step_tools(step)
        if tools and len(tools) > 1:
            # Hinted tool should come first
            first = tools[0].get("function", {}).get("name")
            assert first == "search_knowledge_base"


# ── Reflector ──────────────────────────────────────────────────────────────

class TestReflector:
    """Reflector: 快速通过检查、评估."""

    def test_quick_pass_all_completed(self):
        from app.agent.reflector import Reflector
        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        mock_client = MagicMock()
        reflector = Reflector(mock_client, AgentConfig())

        plan = Plan(id="p1", goal="simple", steps=[
            PlanStep(id="s1", description="s1", status="completed"),
        ])
        results = {"s1": {"result": "done"}}
        assert reflector._quick_pass_check(plan, results) is True

    def test_quick_pass_with_failure(self):
        from app.agent.reflector import Reflector
        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        reflector = Reflector(MagicMock(), AgentConfig())

        plan = Plan(id="p1", goal="failing", steps=[
            PlanStep(id="s1", description="s1", status="failed"),
        ])
        results = {}
        assert reflector._quick_pass_check(plan, results) is False

    def test_quick_pass_too_many_steps(self):
        from app.agent.reflector import Reflector
        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        reflector = Reflector(MagicMock(), AgentConfig())

        plan = Plan(id="p1", goal="many", steps=[
            PlanStep(id=f"s{i}", description=f"s{i}", status="completed")
            for i in range(5)
        ])
        results = {f"s{i}": {"result": "ok"} for i in range(5)}
        assert reflector._quick_pass_check(plan, results) is False

    def test_build_plan_summary(self):
        from app.agent.reflector import Reflector
        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        reflector = Reflector(MagicMock(), AgentConfig())

        plan = Plan(id="p1", goal="test", steps=[
            PlanStep(id="s1", description="step one", status="completed"),
            PlanStep(id="s2", description="step two", status="failed"),
        ])
        summary = reflector._build_plan_summary(plan)
        assert "s1" in summary
        assert "s2" in summary
        assert "step one" in summary

    def test_extract_json_from_code_fence(self):
        from app.agent.reflector import Reflector
        from app.agent.config import AgentConfig
        reflector = Reflector(MagicMock(), AgentConfig())

        text = '```json\n{"decision": "pass", "achievement": 90}\n```'
        result = reflector._extract_json(text)
        assert result["decision"] == "pass"
        assert result["achievement"] == 90


# ── Memory Bridge ──────────────────────────────────────────────────────────

class TestMemoryBridge:
    """AgentMemoryBridge: 步骤结果存储、查询."""

    def bridge(self):
        """Helper: create a bridge with a unique namespace to avoid test pollution."""
        import uuid
        from app.agent.memory_bridge import AgentMemoryBridge
        return AgentMemoryBridge(agent_id=f"test-{uuid.uuid4().hex[:8]}", organization_id=1, user_id=0)

    def test_store_and_get_step_result(self):
        bridge = self.bridge()
        bridge.store_step_result("s1", {"description": "search", "status": "completed", "result": "found"})
        result = bridge.get_step_result("s1")
        assert result is not None
        assert result["description"] == "search"
        assert result["result"] == "found"

    def test_get_nonexistent_step_result(self):
        bridge = self.bridge()
        assert bridge.get_step_result("no_such_step") is None

    def test_get_all_results_empty(self):
        bridge = self.bridge()
        assert bridge.get_all_results() == {}

    def test_get_all_results_after_storing(self):
        bridge = self.bridge()
        bridge.store_step_result("s1", {"result": "data1"})
        bridge.store_step_result("s2", {"result": "data2"})
        results = bridge.get_all_results()
        assert len(results) == 2
        assert results["s1"]["result"] == "data1"

    def test_push_and_pop_task(self):
        bridge = self.bridge()
        bridge.push_task("s1", "do something")
        bridge.push_task("s2", "do another")
        task_wrapper = bridge.pop_task()
        assert task_wrapper is not None
        assert task_wrapper["task"]["step_id"] == "s2"
        assert bridge.pop_task()["task"]["step_id"] == "s1"
        assert bridge.pop_task() is None

    def test_reset_working_memory(self):
        from app.agent.memory_bridge import AgentMemoryBridge
        bridge = AgentMemoryBridge(agent_id="test", organization_id=1, user_id=0)
        bridge.store_step_result("s1", {"result": "data"})
        bridge.reset_working()
        assert bridge.get_all_results() == {}
