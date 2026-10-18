"""issue #281 端到端：POST /workflows/{id}/execute?stream=true（SSE）在真库上可用。

真 SQLite（create_all 建真实 schema；StaticPool 单连接保证请求会话与断言会话同库）
+ 真引擎（不打补丁）+ httpx.ASGITransport。覆盖：

1. 修复前形态（评审 #280 §7 实测）：node_complete 事件携带 executor 原始返回
   （input 节点含 `HumanMessage`）→ 端点事件泵 `json.dumps` 抛
   `TypeError: Object of type HumanMessage is not JSON serializable` → SSE 只到
   `event: error`、执行记录永卡 running、node_results 不落库；
2. 修复后：流到 `event: complete`、消息以可 JSON 化形态出现在事件与落库记录里、
   执行记录 completed；
3. 失败收尾（#281 第二部分要求）：序列化失败时执行记录落 failed（不再卡 running），
   且错误路径对齐 /chat 的 SSE 契约 —— `event: error` 后补 `event: done`
   （agent.py 错误路径同模式，评审 r201p2 实测口径）。

变异判别：
- 还原 `emit_event` 裸传（去掉引擎侧 _to_jsonable）→ 用例 1 红（error 代替 complete）；
- 去掉事件泵 except 里的 failed 收尾 → 用例 2 的"落库 failed"断言红（卡 running）；
- 去掉任一失败出口的 done → 对应用例红。
"""
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_MIN_FLOW = {
    "nodes": [
        {"id": "in1", "type": "input", "data": {"prompt": "{{input}}"}, "position": {"x": 0, "y": 0}},
        {"id": "out1", "type": "output", "data": {}, "position": {"x": 1, "y": 0}},
    ],
    "edges": [{"id": "e1", "source": "in1", "target": "out1"}],
}

# workflow_error 分支：未知节点类型在入口节点的 get_executor 处抛出 → 整图异常
_BAD_TYPE_FLOW = {
    "nodes": [
        {"id": "n0", "type": "bogus_type_281", "data": {}, "position": {"x": 0, "y": 0}},
        {"id": "out1", "type": "output", "data": {}, "position": {"x": 1, "y": 0}},
    ],
    "edges": [{"id": "e1", "source": "n0", "target": "out1"}],
}

# 外层 except 分支：flow_data 直接入库（绕过创建 schema 校验）→ WConfig 装配抛错
_BROKEN_FLOW = {"nodes": "not-a-list", "edges": []}


def _make_user():
    from unittest.mock import MagicMock

    u = MagicMock()
    u.id = 1
    u.username = "wfowner"
    u.organization_id = 1
    u.role = "user"
    u.is_superuser = False
    u.is_active = True
    return u


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    """把 SSE 报文解析为 (event_type, payload) 序列；data 行解析失败即测试失败。"""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event_type, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event_type is not None:
            events.append((event_type, data))
    return events


async def _setup_app(flow_data: dict):
    """建真库 + 注册依赖覆盖；返回 (async client factory, session factory, workflow_id)。"""
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app
    from app.models import Base
    from app.models.workflow import Workflow

    db_engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as s:
        wf = Workflow(name="issue281-stream", flow_data=flow_data, created_by=1, organization_id=1)
        s.add(wf)
        await s.commit()
        wf_id = wf.id

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _make_user()
    return app, db_engine, factory, wf_id


async def _execute_stream(factory, wf_id: int, text: str = "你好") -> httpx.Response:
    from app.main import app

    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as ac:
            return await ac.post(
                f"/api/v1/workflows/{wf_id}/execute",
                params={"stream": "true"},
                json={"text": text},
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stream_minimal_flow_completes_and_persists_node_results():
    """回归主用例：stream=true 端到端 —— node_complete/complete 补齐、消息 JSON 化、终态 completed。

    修复前（评审 #280 §7 实测）：SSE 只到 `event: error`（data 为
    "Object of type HumanMessage is not JSON serializable"），本用例全红。
    """
    from app.models.workflow import WorkflowExecution

    app, db_engine, factory, wf_id = await _setup_app(_MIN_FLOW)
    try:
        r = await _execute_stream(factory, wf_id)
        body = r.text
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse_events(body)  # 每条 data 行都必须能被 json.loads
        types = [t for t, _ in events]
        assert "error" not in types, body[:800]
        assert types[-1] == "complete", types
        assert "done" not in types  # done 是错误路径的终止符；成功路径以 complete 收口
        assert types.count("node_complete") >= 2  # 引擎实时 2 条 + 终态重放 2 条

        # 实时 node_complete（引擎 emit，无 status 字段）：消息以忠实 dict 形态出现
        in1_live = next(
            d for t, d in events
            if t == "node_complete" and d.get("node_id") == "in1" and "status" not in d
        )
        msg = in1_live["output"]["messages"][0]
        assert msg["content"] == "你好"
        assert msg["type"] == "human"

        # 终态 complete 载荷
        complete = next(d for t, d in events if t == "complete")
        assert complete["success"] is True

        # 落库复核：终态 completed（不再卡 running）、node_results 落库且同形态
        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "completed"
        assert ex.error_message is None
        assert ex.completed_at is not None
        assert ex.output_data["success"] is True
        stored = {nr["node_id"]: nr for nr in ex.node_results}
        assert set(stored) == {"in1", "out1"}
        assert stored["in1"]["status"] == "completed"
        assert stored["in1"]["output"]["messages"][0]["content"] == "你好"
    finally:
        await db_engine.dispose()


@pytest.mark.asyncio
async def test_stream_serialization_failure_marks_failed_and_emits_done(monkeypatch):
    """序列化失败（回归模拟）→ 执行记录落 failed（不再卡 running）+ error 后补 done。

    把源头规整函数还原为恒等（等价于修复前的"事件载荷含 HumanMessage"形态）：
    事件泵 json.dumps 抛 TypeError → 此前只发 error 即断流、记录永卡 running；
    修复后必须收尾为 failed 且流以 error + done 结束（对齐 /chat 错误路径）。
    """
    from app.models.workflow import WorkflowExecution

    monkeypatch.setattr("app.services.workflow_engine._to_jsonable", lambda v: v)

    app, db_engine, factory, wf_id = await _setup_app(_MIN_FLOW)
    try:
        r = await _execute_stream(factory, wf_id)
        body = r.text
        assert r.status_code == 200

        events = _parse_sse_events(body)
        types = [t for t, _ in events]
        assert "error" in types, body[:800]
        err = next(d for t, d in events if t == "error")
        assert "HumanMessage is not JSON serializable" in err["error"]
        # SSE 契约：error 后补 done，done 是流终事件；不出现 complete
        assert types[-1] == "done", types
        assert types[-2] == "error", types
        assert "complete" not in types

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed"  # 不再是 running
        assert "not JSON serializable" in (ex.error_message or "")
        assert ex.completed_at is not None
    finally:
        await db_engine.dispose()


@pytest.mark.asyncio
async def test_stream_workflow_error_branch_emits_error_then_done():
    """workflow_error 出口（整图异常）→ 记录 failed + error 后补 done。"""
    from app.models.workflow import WorkflowExecution

    app, db_engine, factory, wf_id = await _setup_app(_BAD_TYPE_FLOW)
    try:
        r = await _execute_stream(factory, wf_id)
        events = _parse_sse_events(r.text)
        types = [t for t, _ in events]
        assert "error" in types, r.text[:800]
        err = next(d for t, d in events if t == "error")
        assert "Unknown node type" in err["error"]
        assert types[-1] == "done"
        assert types[-2] == "error"

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed"
        assert "Unknown node type" in (ex.error_message or "")
        assert ex.completed_at is not None
    finally:
        await db_engine.dispose()


@pytest.mark.asyncio
async def test_stream_setup_failure_outer_branch_emits_error_then_done():
    """外层 except 出口（流程配置装配失败）→ 记录 failed + error 后补 done。"""
    from app.models.workflow import WorkflowExecution

    app, db_engine, factory, wf_id = await _setup_app(_BROKEN_FLOW)
    try:
        r = await _execute_stream(factory, wf_id)
        events = _parse_sse_events(r.text)
        assert [t for t, _ in events] == ["error", "done"], r.text[:800]

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed"
        assert ex.error_message
        assert ex.completed_at is not None
    finally:
        await db_engine.dispose()


@pytest.mark.asyncio
async def test_failed_finalize_survives_poisoned_session(monkeypatch):
    """毒化会话（上一次 commit 因 JSON 列写入失败 → PendingRollback）后，失败收尾
    仍须落 failed —— rollback 先行回归网（评审 V6：去 rollback 先行全绿存活）。

    构造对齐真实事故形态（workflow.py:311-313 注释记载的 datetime 案例同类）：
    workflow_result 分支写 node_results 的 commit 抛 StatementError（不可 JSON 化
    对象进 JSON 列），会话进入 PendingRollback；随后事件泵 except 调
    mark_execution_failed——该助手必须 rollback 先行，否则再 commit 抛
    PendingRollbackError，记录卡 running（同场景旧 bug 原样存活）。
    """
    from langchain_core.messages import HumanMessage

    from app.models.workflow import WorkflowExecution
    from app.services.workflow_engine import WorkflowEngine

    class _PoisonNodeResult:
        """model_dump(mode="json") 仍产出不可 JSON 化值（不规整的漏网形态）。"""

        def model_dump(self, mode="python"):
            return {"node_id": "in1", "output": {"messages": [HumanMessage(content="毒")]}}

    monkeypatch.setattr(WorkflowEngine, "get_node_results", lambda self: [_PoisonNodeResult()])

    app, db_engine, factory, wf_id = await _setup_app(_MIN_FLOW)
    try:
        r = await _execute_stream(factory, wf_id)
        assert r.status_code == 200

        events = _parse_sse_events(r.text)
        types = [t for t, _ in events]
        assert "error" in types, r.text[:800]
        assert types[-1] == "done", types
        assert types[-2] == "error", types
        err = next(d for t, d in events if t == "error")
        assert "not JSON serializable" in err["error"]

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed", (
            "毒化会话下失败收尾必须仍落 failed（rollback 先行；去之则卡 running）"
        )
        assert "not JSON serializable" in (ex.error_message or "")
        assert ex.completed_at is not None
        assert ex.output_data is None, "毒化更新不得泄漏进落库记录"
    finally:
        await db_engine.dispose()
