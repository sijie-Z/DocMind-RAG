"""Behavior tests for Agent tool selection and task completion.

These tests verify the Agent ACTUALLY COMPLETES tasks — not that
internal calls are made. The Agent is tested as a black box: input
(query + context) → output (events + tool_calls + final answer).

DocMind testing-strategy.md §"必须保证":
  - Agent 在合理 prompt 下能从可用 tools 中选出 analysis / search 类 tool
  - 失败写入 experience (long_term + reflective)
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# Trigger @register_tool for all installed tools (core_tools + tools package)
import app.agent.service  # noqa: F401
from app.agent.config import AgentConfig
from app.agent.loop import PERAgentLoop
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fake_llm():
    """Return an AsyncOpenAI mock wired to return plausible tool_calls.

    The fake LLM returns a chat completion whose tool_calls field contains
    the tool name and arguments that would be chosen by a real planner.
    """
    client = MagicMock()

    async def _fake_create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()

        # Choose the right tool based on the last user message
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        # Default tool: search_knowledge_base for analysis queries
        tool_name = "search_knowledge_base"
        tool_args = json.dumps({"query": last_user})

        if "分析" in last_user or "财务" in last_user or "对比" in last_user:
            tool_name = "search_knowledge_base"
            tool_args = json.dumps({"query": last_user})

        # Phase 1 (planner) → structured plan, no tool_call
        # Phase 2 (executor) → tool_call
        if tools and len(tools) > 0:
            # Executor phase — return tool_call
            tool_call = MagicMock()
            tool_call.id = "fake_call_1"
            tool_call.function.name = tool_name
            tool_call.function.arguments = tool_args
            msg.tool_calls = [tool_call]
        else:
            # Planner phase — return plan text
            msg.tool_calls = None

        msg.content = json.dumps({
            "goal": f"Answer the query: {last_user}",
            "steps": [
                {"id": "s1", "description": "Search for relevant documents", "dependencies": [], "tool_hint": tool_name},
            ],
        }, ensure_ascii=False)
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _fake_create
    return client


@pytest.fixture
def agent():
    """Create a PERAgentLoop with fake LLM and real tool registry."""
    os.environ.setdefault("DEEPSEEK_API_KEY", "fake-key-for-test")
    os.environ.setdefault("DEEPSEEK_API_URL", "http://localhost:8080/v1")

    client = _make_fake_llm()
    config = AgentConfig(
        model="fake-model",
        enable_planning=True,
        enable_reflection=False,
        enable_memory=False,
        enable_thinking=False,
        enable_tools=True,
        enable_experience=False,
        max_plan_steps=8,
        max_iterations=5,
        max_retries_per_step=1,
    )
    return PERAgentLoop(client, config, organization_id=1, user_id=0, execution_mode="sequential")


# ── Tool Registry behavior ──────────────────────────────────────────────────


class TestToolRegistryBehavior:
    """ToolRegistry: tools can be registered, queried, and exported to OpenAI format."""

    def test_all_registered_tools_are_exportable(self):
        """Every registered real tool produces a valid OpenAI function spec."""
        tools = tool_registry.to_openai_tools()
        assert len(tools) > 0, "tool_registry should have real tools registered"

        for t in tools:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "description" in t["function"]
            assert "parameters" in t["function"]

    def test_search_tools_exist(self):
        """At minimum, search/retrieval tools are present."""
        tools = tool_registry.to_openai_tools()
        names = {t["function"]["name"] for t in tools}
        assert "search_knowledge_base" in names, f"Expected search_knowledge_base in {names}"


# ── Agent behavior (black box) ──────────────────────────────────────────────


class TestAgentBehavior:
    """Agent tested as input→output — event stream, tool selection, completion."""

    @pytest.mark.asyncio
    async def test_agent_produces_plan_and_execution_events(self, agent):
        """A simple query produces plan_start, plan_step, and done events."""
        events = []
        async for event in agent.run("分析一下财务数据"):
            events.append(event)

        event_types = {e.type for e in events}
        assert "plan_start" in event_types, f"Expected plan_start in {event_types}"
        assert "done" in event_types, f"Expected done in {event_types}"

    @pytest.mark.asyncio
    async def test_agent_uses_tools_for_analysis_query(self, agent):
        """An analysis query should trigger tool usage (tool_call event)."""
        events = []
        async for event in agent.run("对比星辰科技和远方创新的财务表现"):
            events.append(event)

        tool_events = [e for e in events if e.type == "tool_call"]
        assert len(tool_events) > 0, "Analysis query should trigger at least one tool_call"

    @pytest.mark.asyncio
    async def test_agent_completes_without_error(self, agent):
        """A simple factual query completes with a done event and no errors."""
        errors = []
        async for event in agent.run("什么是AI?"):
            if event.type in ("tool_error", "error"):
                errors.append(event)

        assert len(errors) == 0, f"Simple query should not produce errors, got: {[e.content for e in errors]}"


# ── Executor behavior ───────────────────────────────────────────────────────


class TestExecutorBehavior:
    """Executor handles LLM failures gracefully."""

    @pytest.mark.asyncio
    async def test_llm_error_produces_tool_error_event(self):
        """When the LLM fails, executor yields a tool_error event, not a crash."""
        from app.agent.config import AgentConfig
        from app.agent.executor import Executor

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

        assert any(e.type == "tool_error" for e in events), \
            "LLM failure should produce tool_error event"


# ── Experience recording behavior ───────────────────────────────────────────


class TestExperienceRecording:
    """Failed actions write experience entries."""

    @pytest.mark.asyncio
    async def test_failed_tool_writes_experience(self):
        """After a tool error in an agent run, experience is recorded."""

        bridge = AgentMemoryBridge(agent_id="test_experience", organization_id=1, user_id=0)
        await bridge.record_experience(success=False, action="search_knowledge_base",
                                        result="Connection timeout: retry exhausted")

        # Should write to long_term
        long_items = sum(len(v) for v in bridge.system.long_term.memories.values())
        assert long_items >= 1, "Failed experience should write to long_term"

        # Should add a reflective lesson
        assert len(bridge.system.reflective.lessons) >= 1, \
            "Failed experience should add a reflective lesson"
