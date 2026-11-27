"""批次十四 C4：配额强制执行的 HTTP 入口契约（sqlite 全链，不连真实服务）。

覆盖：
- 超限时 chat/stream、agent/chat、agent resume、workflow execute、
  monitoring rag-eval single/batch 都在开流 / LLM 之前返回 HTTP 429；
- 错误信封：error_code=ORG_TOKEN_QUOTA_EXCEEDED，detail 带 used/quota/reset_at；
- 跨组织隔离：A 组织超限不影响 B 组织；
- 超管旁路在入口层放行（审计由服务层单测覆盖）。

变异判别力：任一入口删掉 ensure_monthly_quota 调用 → 对应用例红；配额语义
改成 `>`/忽略 NULL → 服务层单测与入口用例红。
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.models.token_usage import TokenUsageRecord
from app.models.workflow import Workflow

CHAT_STREAM = "/api/v1/chat/stream"
AGENT_CHAT = "/api/v1/agent/chat"
AGENT_RESUME = "/api/v1/agent/tasks/t-missing/resume"
WORKFLOW_EXECUTE = "/api/v1/workflows/1/execute"
RAG_EVAL = "/api/v1/monitoring/rag-eval"
RAG_EVAL_BATCH = "/api/v1/monitoring/rag-eval-batch"
SCHED_RUN_NOW = "/api/v1/scheduled-tasks/sched-over/run-now"


def _make_user(uid=1, org=1, superuser=False):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=org,
        is_superuser=superuser,
        is_active=True,
        role="user",
    )


@pytest.fixture
async def api():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Organization(id=1, name="org-a", owner_id=1),
                Organization(id=2, name="org-b", owner_id=2),
                # 组织 3：未超限（工作流/记账正例用；工作流按 created_by 可见）
                Organization(id=3, name="org-c", owner_id=3),
                # 组织 1：配额 100，本月已用 150（超限）
                OrganizationSettings(organization_id=1, monthly_token_quota=100),
                # 组织 2：配额 100，本月已用 10（未超限）
                OrganizationSettings(organization_id=2, monthly_token_quota=100),
                OrganizationSettings(organization_id=3, monthly_token_quota=100),
                TokenUsageRecord(
                    user_id=1,
                    organization_id=1,
                    model="m",
                    source="rag_chat",
                    input_tokens=150,
                    output_tokens=0,
                    created_at=datetime.now(UTC),
                ),
                TokenUsageRecord(
                    user_id=2,
                    organization_id=2,
                    model="m",
                    source="rag_chat",
                    input_tokens=10,
                    output_tokens=0,
                    created_at=datetime.now(UTC),
                ),
                TokenUsageRecord(
                    user_id=3,
                    organization_id=3,
                    model="m",
                    source="rag_chat",
                    input_tokens=10,
                    output_tokens=0,
                    created_at=datetime.now(UTC),
                ),
                Workflow(
                    id=1,
                    name="wf",
                    organization_id=1,
                    created_by=1,
                    is_active=True,
                    flow_data={"nodes": [], "edges": []},
                ),
                Workflow(
                    id=2,
                    name="wf-ok",
                    organization_id=3,
                    created_by=3,
                    is_active=True,
                    flow_data={"nodes": [], "edges": []},
                ),
                ScheduledTask(
                    id="sched-over",
                    name="超限任务",
                    prompt="p",
                    schedule_type=ScheduleType.CRON,
                    cron_expr="0 9 * * *",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=datetime(2026, 12, 1, 9, 0),
                    created_by=1,
                    organization_id=1,
                ),
            ]
        )
        await s.commit()

    state = SimpleNamespace(user=_make_user())

    async def _override_db():
        async with factory() as s:
            yield s

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield SimpleNamespace(client=ac, state=state, factory=factory)
    app.dependency_overrides.clear()
    await engine.dispose()


def _assert_quota_429(response: httpx.Response, *, used=150, quota=100) -> None:
    assert response.status_code == 429, response.text
    assert response.headers.get("content-type", "").startswith("application/json"), (
        "配额拒绝必须是 JSON 429，不能是 text/event-stream 半截流"
    )
    body = response.json()
    assert body["error_code"] == "ORG_TOKEN_QUOTA_EXCEEDED", body
    assert body["success"] is False
    assert body["detail"]["used"] == used
    assert body["detail"]["quota"] == quota
    assert isinstance(body["detail"]["reset_at"], str) and body["detail"]["reset_at"]


# ── 四个 HTTP 入口（+ 批量评测）────────────────────────────────────────────


async def test_chat_stream_quota_429(api):
    r = await api.client.post(CHAT_STREAM, json={"content": "你好"})
    _assert_quota_429(r)


async def test_chat_stream_replay_skips_quota(api):
    """Last-Event-ID 回放不产生 LLM 成本：超限组织仍能拿到 200 回放流。"""
    r = await api.client.post(
        CHAT_STREAM,
        json={"content": "你好"},
        headers={"Last-Event-ID": "conv-x:5"},
    )
    assert r.status_code == 200, r.text
    assert "ORG_TOKEN_QUOTA_EXCEEDED" not in r.text


async def test_chat_stream_deep_analysis_redirect_skips_quota(api, monkeypatch):
    """旧客户端 useAgent 重定向不调 LLM：应得到迁移错误码而不是配额 429。"""
    import app.api.v1.endpoints.chat as chat_endpoint

    monkeypatch.setattr(chat_endpoint, "DEEP_ANALYSIS_GUARD_ENABLED", True)
    r = await api.client.post(CHAT_STREAM, json={"content": "你好", "useAgent": True})
    assert r.status_code == 200, r.text
    assert "DEEP_ANALYSIS_MOVED_TO_AGENT" in r.text
    assert "ORG_TOKEN_QUOTA_EXCEEDED" not in r.text


async def test_agent_chat_quota_429(api):
    r = await api.client.post(AGENT_CHAT, json={"query": "你好"})
    _assert_quota_429(r)


async def test_agent_resume_quota_429(api):
    """resume 在认领/开流前拒绝：不存在的 task 也应先得到 429 而不是 404？"""
    # 语义：配额检查在 prepare_resume 之前；本用例只钉「入口确实检查」。
    r = await api.client.post(AGENT_RESUME)
    _assert_quota_429(r)


async def test_workflow_execute_quota_429(api):
    r = await api.client.post(WORKFLOW_EXECUTE, json={})
    _assert_quota_429(r)


async def test_rag_eval_single_quota_429(api):
    r = await api.client.post(RAG_EVAL, json={"question": "q", "answer": "a", "context": []})
    _assert_quota_429(r)


async def test_rag_eval_batch_quota_429(api):
    r = await api.client.post(
        RAG_EVAL_BATCH,
        json={"items": [{"question": "q", "answer": "a", "context": []}]},
    )
    _assert_quota_429(r)


# ── 跨组织隔离 / 超管旁路（入口层）────────────────────────────────────────


async def test_cross_org_quota_isolation(api):
    """A 组织超限 429；B 组织（未超限）不被 A 的组织策略拖累。"""
    api.state.user = _make_user(uid=2, org=2)
    with patch(
        "app.services.answer_quality_eval_service.evaluate_single",
        new=AsyncMock(
            return_value={
                "faithfulness": 0.9,
                "relevancy": 0.8,
                "context_precision": 0.7,
                "details": {},
            }
        ),
    ):
        r = await api.client.post(RAG_EVAL, json={"question": "q", "answer": "a", "context": []})
    assert r.status_code == 200, r.text
    assert r.json()["faithfulness"] == 0.9


async def test_superuser_bypasses_quota_at_entry(api, monkeypatch):
    from app.services.audit_service import audit_service

    calls: list[dict] = []

    async def _capture(user_id, action, **kwargs):
        calls.append({"user_id": user_id, "action": action, **kwargs})

    monkeypatch.setattr(audit_service, "log_activity", _capture)
    api.state.user = _make_user(uid=99, org=1, superuser=True)
    with patch(
        "app.services.answer_quality_eval_service.evaluate_single",
        new=AsyncMock(
            return_value={
                "faithfulness": 1.0,
                "relevancy": 1.0,
                "context_precision": 1.0,
                "details": {},
            }
        ),
    ):
        r = await api.client.post(RAG_EVAL, json={"question": "q", "answer": "a", "context": []})
    assert r.status_code == 200, r.text
    assert [c["action"] for c in calls] == ["org_token_quota_bypass"]


# ── WebSocket：错误帧而不是 HTTP 500 ──────────────────────────────────────


def test_websocket_quota_sends_error_frame_not_500(client):
    """WS 超限：结构化 error 帧（code + detail），连接不断、不抛未处理异常。"""
    from app.core.ws_auth import WsUserState
    from app.main import app

    state = WsUserState(id=1, is_active=True, organization_id=1, is_superuser=False)
    row_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: SimpleNamespace(monthly_token_quota=100))
    )
    usage_result = SimpleNamespace(scalar=lambda: 150)
    fake_session = SimpleNamespace(execute=AsyncMock(side_effect=[row_result, usage_result]))

    class _Ctx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *exc):
            return False

    with (
        patch(
            "app.api.v1.endpoints.chat.authenticate_ws",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "app.api.v1.endpoints.chat.load_active_ws_user",
            new=AsyncMock(return_value=state),
        ),
        patch("app.api.v1.endpoints.chat.AsyncSessionLocal", new=lambda: _Ctx()),
        client.websocket_connect("/api/v1/chat/ws") as ws,
    ):
        ws.send_json({"content": "你好"})
        payload = ws.receive_json()

    assert payload["type"] == "error"
    assert payload["code"] == "ORG_TOKEN_QUOTA_EXCEEDED"
    assert payload["detail"]["used"] == 150
    assert payload["detail"]["quota"] == 100
    assert payload["detail"]["reset_at"]
    app.dependency_overrides.clear()


# ── 定时任务 / 审批续跑 / Agent 回放入口 ────────────────────────────────


async def test_scheduled_run_now_quota_429(api):
    """run-now 触发即会调用 LLM：超限组织在触发前 429，不启动后台运行。"""
    with patch(
        "app.api.v1.endpoints.scheduled_tasks.scheduler_engine.run_now",
        new=AsyncMock(),
    ) as run_now:
        r = await api.client.post(SCHED_RUN_NOW)
    _assert_quota_429(r)
    run_now.assert_not_awaited()


async def test_approval_and_plan_approval_quota_429(api):
    """审批续跑与 resume 同口径：记账/认领前 429。"""
    for path, body in (
        ("/api/v1/agent/tasks/t-missing/approval", {"decision": "approve"}),
        ("/api/v1/agent/tasks/t-missing/plan-approval", {"decision": "approve"}),
    ):
        r = await api.client.post(path, json=body)
        _assert_quota_429(r)


async def test_agent_chat_replay_skips_quota(api):
    """Agent /chat 的 Last-Event-ID 纯回放不调用 LLM，超限组织仍返回 200。"""
    r = await api.client.post(
        AGENT_CHAT,
        json={"query": "你好"},
        headers={"Last-Event-ID": "session-x:3"},
    )
    assert r.status_code == 200, r.text
    assert "ORG_TOKEN_QUOTA_EXCEEDED" not in r.text


# ── B1：workflow / eval 的 LLM 用量必须入账 ─────────────────────────────


class _FakeWorkflowEngine:
    """替身：返回固定 LLM 节点用量；fail=True 模拟引擎异常。"""

    fail = False

    def __init__(self, config, organization_id=None, user_id=None):
        self.config = config
        self.organization_id = organization_id
        self.user_id = user_id

    def set_event_callback(self, callback):
        self.callback = callback

    async def execute(self, input_data=None):
        if type(self).fail:
            raise RuntimeError("boom")
        return {
            "success": True,
            "output": "ok",
            "node_outputs": {},
            "context": {},
            "memory": {},
        }

    def get_node_results(self):
        from app.schemas.workflow import NodeResult

        return [
            NodeResult(
                node_id="llm1",
                node_type="llm_deepseek",
                status="completed",
                tokens_in=11,
                tokens_out=7,
                model="m-work",
            ),
            NodeResult(
                node_id="llm2",
                node_type="llm_openai",
                status="completed",
                tokens_in=1,
                tokens_out=2,
                model="m-work",
            ),
            # 非 LLM 节点即便带 token 字段也不得计入（口径：只记 LLM 节点）。
            NodeResult(
                node_id="in1",
                node_type="input",
                status="completed",
                tokens_in=999,
                tokens_out=999,
                model="ignored",
            ),
        ]


def _workflow_patches():
    return (
        patch("app.api.v1.endpoints.workflow.WorkflowEngine", new=_FakeWorkflowEngine),
        patch(
            "app.api.v1.endpoints.token_usage.record_token_usage",
            new=AsyncMock(),
        ),
    )


async def test_workflow_execute_records_llm_usage_non_stream(api):
    api.state.user = _make_user(uid=3, org=3)
    p_engine, p_record = _workflow_patches()
    with p_engine, p_record as spy:
        r = await api.client.post(
            "/api/v1/workflows/2/execute", params={"stream": "false"}, json={}
        )
    assert r.status_code == 200, r.text
    assert spy.await_count == 1, "一次工作流执行只记一次账"
    kwargs = spy.await_args.kwargs
    assert kwargs["source"] == "workflow"
    assert kwargs["feature"] == "workflow"
    assert kwargs["organization_id"] == 3
    assert kwargs["user_id"] == 3
    assert kwargs["model"] == "m-work"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 9)


async def test_workflow_stream_records_llm_usage(api):
    api.state.user = _make_user(uid=3, org=3)
    p_engine, p_record = _workflow_patches()
    with p_engine, p_record as spy:
        r = await api.client.post("/api/v1/workflows/2/execute", json={})
    assert r.status_code == 200, r.text
    assert "event: complete" in r.text
    assert spy.await_count == 1
    kwargs = spy.await_args.kwargs
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 9)
    assert kwargs["source"] == "workflow"


async def test_workflow_stream_failure_still_records_usage(api):
    """引擎异常前已完成的 LLM 节点用量也要入账（失败收尾不丢账）。"""
    api.state.user = _make_user(uid=3, org=3)
    _FakeWorkflowEngine.fail = True
    p_engine, p_record = _workflow_patches()
    try:
        with p_engine, p_record as spy:
            r = await api.client.post("/api/v1/workflows/2/execute", json={})
    finally:
        _FakeWorkflowEngine.fail = False
    assert r.status_code == 200, r.text
    assert "event: error" in r.text
    assert spy.await_count == 1
    assert spy.await_args.kwargs["source"] == "workflow"


class _FakeEvalResult:
    faithfulness = 0.9
    relevancy = 0.8
    context_precision = 0.7
    details = {}


class _FakeEvalBatch:
    count = 1
    avg_faithfulness = 0.9
    avg_relevancy = 0.8
    avg_context_precision = 0.7
    failed_count = 0
    failures: list = []
    results: list = []


class _FakeRAGEvaluator:
    """替身：带 usage_by_model；可配置 evaluate/evaluate_batch 抛错。"""

    raise_single = False
    raise_batch = False

    def __init__(self, client=None, model=None):
        self.client = client
        self.model = model
        self.usage_by_model = {"judge-m": (12, 3)}

    async def evaluate(self, question, answer, context):
        if type(self).raise_single:
            from app.rag.errors import EvalJudgeUnavailableError

            raise EvalJudgeUnavailableError("judge down")
        return _FakeEvalResult()

    async def evaluate_batch(self, items):
        if type(self).raise_batch:
            from app.rag.errors import EvalJudgeContractError

            raise EvalJudgeContractError("bad contract")
        return _FakeEvalBatch()


def _eval_patches():
    return (
        patch("app.rag.evaluator.RAGEvaluator", new=_FakeRAGEvaluator),
        patch(
            "app.api.v1.endpoints.token_usage.record_token_usage",
            new=AsyncMock(),
        ),
    )


async def test_rag_eval_single_records_usage(api):
    api.state.user = _make_user(uid=2, org=2)
    p_eval, p_record = _eval_patches()
    with p_eval, p_record as spy:
        r = await api.client.post(RAG_EVAL, json={"question": "q", "answer": "a", "context": []})
    assert r.status_code == 200, r.text
    assert spy.await_count == 1
    kwargs = spy.await_args.kwargs
    assert kwargs["source"] == "rag_eval"
    assert kwargs["feature"] == "rag_eval"
    assert kwargs["organization_id"] == 2
    assert kwargs["model"] == "judge-m"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 3)


async def test_rag_eval_single_failure_still_records_usage(api):
    """失败条目已产生的 token 也要记账（finally 路径）。"""
    api.state.user = _make_user(uid=2, org=2)
    _FakeRAGEvaluator.raise_single = True
    p_eval, p_record = _eval_patches()
    try:
        with p_eval, p_record as spy:
            r = await api.client.post(
                RAG_EVAL, json={"question": "q", "answer": "a", "context": []}
            )
    finally:
        _FakeRAGEvaluator.raise_single = False
    assert r.status_code == 502, r.text
    assert spy.await_count == 1
    assert spy.await_args.kwargs["source"] == "rag_eval"


async def test_rag_eval_batch_records_usage(api):
    api.state.user = _make_user(uid=2, org=2)
    p_eval, p_record = _eval_patches()
    with p_eval, p_record as spy:
        r = await api.client.post(
            RAG_EVAL_BATCH,
            json={"items": [{"question": "q", "answer": "a", "context": []}]},
        )
    assert r.status_code == 200, r.text
    assert spy.await_count == 1
    assert spy.await_args.kwargs["source"] == "rag_eval"


async def test_rag_eval_batch_failure_still_records_usage(api):
    api.state.user = _make_user(uid=2, org=2)
    _FakeRAGEvaluator.raise_batch = True
    p_eval, p_record = _eval_patches()
    try:
        with p_eval, p_record as spy:
            r = await api.client.post(
                RAG_EVAL_BATCH,
                json={"items": [{"question": "q", "answer": "a", "context": []}]},
            )
    finally:
        _FakeRAGEvaluator.raise_batch = False
    assert r.status_code == 502, r.text
    assert spy.await_count == 1
    assert spy.await_args.kwargs["source"] == "rag_eval"


# ── 流式工作流断连收口（安全 blocker）────────────────────────────────────


def _make_slow_engine(node_results):
    """LLM 节点已完成/阻塞中的引擎替身：execute 永久挂起直到被取消。"""

    import asyncio

    class _SlowEngine:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        def __init__(self, config, organization_id=None, user_id=None):
            self.results = list(node_results)

        def set_event_callback(self, callback):
            self.callback = callback

        async def execute(self, input_data=None):
            type(self).started.set()
            await self.callback("node_start", {"node_id": "llm1"})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                type(self).cancelled.set()
                raise

        def get_node_results(self):
            return self.results

    return _SlowEngine


async def _start_stream_then_disconnect(api, engine_cls):
    """直接驱动端点生成器：读两帧后 aclose，模拟客户端断开。"""
    import asyncio

    from app.api.v1.endpoints import workflow as workflow_ep

    recorded = AsyncMock()
    with (
        patch.object(workflow_ep, "WorkflowEngine", engine_cls),
        patch("app.api.v1.endpoints.token_usage.record_token_usage", new=recorded),
        patch.object(workflow_ep, "AsyncSessionLocal", api.factory),
    ):
        async with api.factory() as session:
            response = await workflow_ep.execute_workflow(
                workflow_id=2,
                request=None,
                body={},
                stream=True,
                current_user=_make_user(uid=3, org=3),
                db=session,
            )
            gen = response.body_iterator
            first = await gen.__anext__()
            assert "workflow_start" in first
            second = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert "node_start" in second
            assert engine_cls.started.is_set()
            await gen.aclose()
    return recorded


async def _latest_execution(api):
    from sqlalchemy import select

    from app.models.workflow import WorkflowExecution

    async with api.factory() as db:
        return (
            (await db.execute(select(WorkflowExecution).order_by(WorkflowExecution.id.desc())))
            .scalars()
            .first()
        )


async def test_stream_workflow_disconnect_cancels_run_and_finalizes(api):
    """断连：后台 run task 取消、执行记录脱离 running、已发生 usage 入账。"""
    from app.schemas.workflow import NodeResult

    engine_cls = _make_slow_engine(
        [
            NodeResult(
                node_id="llm1",
                node_type="llm_deepseek",
                status="completed",
                tokens_in=7,
                tokens_out=3,
                model="m-work",
            )
        ]
    )
    recorded = await _start_stream_then_disconnect(api, engine_cls)

    assert engine_cls.cancelled.is_set(), "断连后后台 run_workflow 必须被取消"
    execution = await _latest_execution(api)
    assert execution is not None
    assert execution.status != "running", "断连后执行记录不得永卡 running"
    assert execution.status == "failed"
    assert "客户端断开" in (execution.error_message or "")

    assert recorded.await_count == 1, "断连收口必须补记已发生的 LLM 用量"
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "workflow"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (7, 3)
    assert kwargs["organization_id"] == 3


async def test_stream_workflow_disconnect_without_usage_still_finalizes(api):
    """无 LLM 用量：不写 token 账，但执行记录仍必须落终态。"""
    engine_cls = _make_slow_engine([])
    recorded = await _start_stream_then_disconnect(api, engine_cls)

    assert engine_cls.cancelled.is_set()
    execution = await _latest_execution(api)
    assert execution is not None and execution.status == "failed"
    assert "客户端断开" in (execution.error_message or "")
    recorded.assert_not_awaited()


async def test_stream_workflow_asgi23_disconnect_shielded_finalizes(tmp_path):
    """真实 StreamingResponse.__call__ + ASGI spec 2.3 http.disconnect。

    复审 blocker 的生产证据：Starlette 在 http.disconnect 后于 anyio 已取消的
    cancel scope 中关闭生成器；收口必须在 ``CancelScope(shield=True)`` 内完成，
    否则 DB 终态/记账的 await 会立即再抛 CancelledError，execution 永卡 running。
    """
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.v1.endpoints import workflow as workflow_ep
    from app.core.database import Base
    from app.models.workflow import Workflow, WorkflowExecution
    from app.schemas.workflow import NodeResult

    db_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wf_asgi23.db'}")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(
            Workflow(
                id=2,
                name="wf",
                organization_id=3,
                created_by=3,
                is_active=True,
                flow_data={"nodes": [], "edges": []},
            )
        )
        await db.commit()

    engine_cls = _make_slow_engine(
        [
            NodeResult(
                node_id="llm1",
                node_type="llm_deepseek",
                status="completed",
                tokens_in=7,
                tokens_out=3,
                model="m-work",
            )
        ]
    )
    recorded = AsyncMock()
    try:
        async with factory() as db:
            with (
                patch.object(workflow_ep, "WorkflowEngine", engine_cls),
                patch.object(workflow_ep, "AsyncSessionLocal", factory),
                patch("app.api.v1.endpoints.token_usage.record_token_usage", new=recorded),
            ):
                response = await workflow_ep.execute_workflow(
                    workflow_id=2,
                    request=None,
                    body={},
                    stream=True,
                    current_user=_make_user(uid=3, org=3),
                    db=db,
                )

                body_count = 0
                second_body = asyncio.Event()

                async def send(message):
                    nonlocal body_count
                    if message["type"] == "http.response.body" and message.get("body"):
                        body_count += 1
                        if body_count == 2:
                            second_body.set()

                async def receive():
                    # ASGI 2.3：连接先建立；读两帧后客户端断开。
                    await second_body.wait()
                    return {"type": "http.disconnect"}

                scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
                response_task = asyncio.create_task(response(scope, receive, send))
                try:
                    await asyncio.wait_for(response_task, timeout=10)
                except BaseException:  # noqa: BLE001 — Starlette 以 ClientDisconnect/Cancelled 结束
                    pass
                assert response_task.done(), "断连后 response task 必须结束"
                await asyncio.wait_for(engine_cls.cancelled.wait(), timeout=5)

        async with factory() as check_db:
            execution = (
                (
                    await check_db.execute(
                        select(WorkflowExecution).order_by(WorkflowExecution.id.desc())
                    )
                )
                .scalars()
                .first()
            )
    finally:
        await db_engine.dispose()

    assert engine_cls.cancelled.is_set(), "断连后 run_workflow 必须被取消"
    assert execution is not None
    assert execution.status == "failed", "ASGI 2.3 断连后 execution 不得停在 running"
    assert "客户端断开" in (execution.error_message or "")
    assert recorded.await_count == 1, "shield 收口必须补记一次已发生的 LLM 用量"
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "workflow"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (7, 3)
    assert kwargs["organization_id"] == 3
