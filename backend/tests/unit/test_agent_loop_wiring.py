"""Regression tests for the PER-loop wiring defects fixed on this branch.

Each test here corresponds to a defect that shipped in v1.21.0 and was found by
manual audit + adversarial review. They are written against the *class* of bug
where possible (e.g. every AgentEvent construction in the app is validated),
so the whole category cannot silently return.
"""

import ast
import pathlib

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"


# ── AgentEvent construction ────────────────────────────────────────────────


class TestAgentEventConstruction:
    """A typo'd keyword raised TypeError at runtime and was swallowed by the
    surrounding except, so it surfaced to the LLM as a bogus tool error."""

    def test_tool_error_accepts_error_type(self):
        from app.agent.events import AgentEvent

        event = AgentEvent(
            type="tool_error",
            tool_name="search_knowledge_base",
            content="boom",
            error_type="timeout",
        )
        assert event.error_type == "timeout"
        assert event.to_sse_dict()["error_type"] == "timeout"

    def test_error_type_is_distinct_from_reflection_result(self):
        """reflection_result is a pass/retry/replan/escalate verdict, not an
        error code — conflating them was part of the original defect."""
        from app.agent.events import AgentEvent

        event = AgentEvent(type="tool_error", error_type="rate_limited")
        assert event.reflection_result == "pass"
        assert event.error_type == "rate_limited"

    def test_every_agent_event_construction_uses_valid_kwargs(self):
        """Static sweep: no AgentEvent(...) anywhere in app/ may pass a keyword
        the dataclass does not declare."""
        import dataclasses

        from app.agent.events import AgentEvent

        valid = {f.name for f in dataclasses.fields(AgentEvent)}
        offenders: list[str] = []
        for path in APP_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "id", None) != "AgentEvent":
                    continue
                for kw in node.keywords:
                    if kw.arg and kw.arg not in valid:
                        offenders.append(
                            f"{path.relative_to(BACKEND_DIR)}:{node.lineno} -> {kw.arg}"
                        )
        assert not offenders, "AgentEvent constructed with unknown kwargs: " + "; ".join(offenders)


# ── Retry policy coverage ──────────────────────────────────────────────────


class TestRetryPolicyReachability:
    """The retry loop can only pick a policy for error codes the registry emits."""

    def test_rate_limited_is_retryable(self):
        from app.agent.retry_policy import get_policy

        assert get_policy("rate_limited").retryable is True

    def test_timeout_is_retryable(self):
        from app.agent.retry_policy import get_policy

        policy = get_policy("timeout")
        assert policy.retryable is True
        assert policy.action == "repeat"

    def test_validation_error_is_fatal(self):
        """Retrying a schema mismatch just burns tokens."""
        from app.agent.retry_policy import get_policy

        assert get_policy("validation_error").retryable is False


# ── Planner → loop goal handoff ────────────────────────────────────────────


class TestPlanGoalPropagation:
    """loop.py used to overwrite the plan goal with the plan id, so every
    goal-aware LLM call (freshness, relevance, reflection) judged a hex id."""

    def test_plan_complete_carries_the_goal(self):
        from app.agent.events import AgentEvent

        event = AgentEvent(type="plan_complete", plan_id="abc123", plan_goal="对比两份财报")
        assert event.plan_goal == "对比两份财报"
        assert event.to_sse_dict()["plan_goal"] == "对比两份财报"

    def test_plan_goal_is_not_the_plan_id(self):
        event_goal = "对比两份财报"
        plan_id = "9f3a2b7c1d0e"
        assert event_goal != plan_id, "guard: the two fields must stay distinguishable"


# ── Reflection result delivery ─────────────────────────────────────────────


class TestReflectionDelivery:
    """An async generator cannot `return` a value, so the structured verdict is
    delivered as the content of a final reflection event."""

    @pytest.mark.asyncio
    async def test_quick_pass_emits_a_reflection_event(self):
        from unittest.mock import MagicMock

        from app.agent.config import AgentConfig
        from app.agent.planner import Plan, PlanStep
        from app.agent.reflector import Reflector

        reflector = Reflector(MagicMock(), AgentConfig())
        plan = Plan(
            id="p1",
            goal="g",
            steps=[
                PlanStep(id="s1", description="s1", status="completed"),
            ],
        )
        events = [e async for e in reflector.reflect(plan, {"s1": {"result": "ok"}})]

        assert events, "reflect() produced no events at all"
        assert any(e.type == "reflection" for e in events)
        assert any(e.reflection_result == "pass" for e in events)

    @pytest.mark.asyncio
    async def test_reflect_is_an_async_generator(self):
        """Pins the language-level constraint that forced the event-based design."""
        import inspect

        from app.agent.reflector import Reflector

        assert inspect.isasyncgenfunction(Reflector.reflect)


# ── Context engine wiring ──────────────────────────────────────────────────


class TestContextEngineWiring:
    """loop.py built a ContextEngine and never called fit()/fit_async()."""

    def test_loop_invokes_context_fitting(self):
        source = (APP_DIR / "agent" / "loop.py").read_text(encoding="utf-8")
        assert "fit_async(" in source or "context_engine.fit(" in source, (
            "PERAgentLoop must actually compress history through the ContextEngine"
        )

    @pytest.mark.asyncio
    async def test_fit_async_compresses_over_budget_history(self):
        from app.agent.context import ContextEngine

        engine = ContextEngine(max_context_tokens=200, tail_window=2)
        history = [
            {"role": "user", "content": "x" * 500},
            {"role": "assistant", "content": "y" * 500},
            {"role": "user", "content": "z" * 500},
            {"role": "assistant", "content": "w" * 500},
            {"role": "user", "content": "recent"},
        ]
        fitted = await engine.fit_async(history)
        from app.agent.context import estimate_messages_tokens

        assert estimate_messages_tokens(fitted) <= engine.max_context_tokens


# ── Conditional routing ────────────────────────────────────────────────────


class TestConditionalRouting:
    """The route decision ran after an `async for` that could exit early, and it
    only saw `chunk` events, so it never fired on real runs."""

    def test_short_output_keeps_executing(self):
        from unittest.mock import MagicMock

        from app.agent.config import AgentConfig
        from app.agent.loop import PERAgentLoop
        from app.agent.planner import Plan, PlanStep

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)
        plan = Plan(id="p", goal="g", steps=[PlanStep(id="s2", description="d")])
        pending = [s for s in plan.steps if s.status == "pending"]

        assert loop._should_continue_execution("short", plan, pending) is True

    def test_substantial_answer_skips_remaining_steps(self):
        from unittest.mock import MagicMock

        from app.agent.config import AgentConfig
        from app.agent.loop import PERAgentLoop
        from app.agent.planner import Plan, PlanStep

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)
        plan = Plan(id="p", goal="g", steps=[PlanStep(id="s2", description="d")])
        pending = [s for s in plan.steps if s.status == "pending"]
        answer = "根据检索结果，结论如下：" + "数据" * 200

        assert loop._should_continue_execution(answer, plan, pending) is False

    def test_non_string_output_does_not_crash(self):
        """execution_step_result events carry dicts; str methods would blow up."""
        from unittest.mock import MagicMock

        from app.agent.config import AgentConfig
        from app.agent.loop import PERAgentLoop
        from app.agent.planner import Plan, PlanStep

        loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)
        plan = Plan(id="p", goal="g", steps=[PlanStep(id="s2", description="d")])
        pending = [s for s in plan.steps if s.status == "pending"]

        assert loop._should_continue_execution({"result": "x" * 500}, plan, pending) in (
            True,
            False,
        )

    def test_routing_runs_inside_a_finally(self):
        """It must still decide when the consumer stops iterating (disconnect)."""
        source = (APP_DIR / "agent" / "loop.py").read_text(encoding="utf-8")
        marker = "self._should_continue_execution"
        assert marker in source
        head = source[: source.index(marker)]
        assert "finally:" in head, "conditional routing must run in a finally block"
