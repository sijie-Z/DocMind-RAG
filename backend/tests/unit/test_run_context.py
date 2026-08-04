"""Unit tests for the agent RunContext object."""

from app.agent.run_context import RunContext


def test_run_context_roundtrip():
    ctx = RunContext(
        user_id=7,
        organization_id=3,
        session_id="s1",
        agent_id="analyst",
        config={"temperature": 0.2},
    )

    data = ctx.to_dict()
    assert data["user_id"] == 7
    assert data["organization_id"] == 3
    assert data["session_id"] == "s1"
    assert data["agent_id"] == "analyst"
    assert data["config"]["temperature"] == 0.2


def test_run_context_summary():
    ctx = RunContext(user_id=1, organization_id=2, session_id="abc", agent_id="default")
    assert ctx.summary() == "user=1 org=2 agent=default session=abc"

    no_session = RunContext(user_id=1, organization_id=2)
    assert "-" in no_session.summary()
