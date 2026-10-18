"""issue #276 端到端：POST /workflows/{id}/execute（非流式）在真库上跑通最小工作流。

真 SQLite（create_all 建真实 schema；StaticPool 单连接保证请求会话与断言会话同库）
+ 真引擎（不打补丁）+ httpx.ASGITransport。验证两端：

1. 引擎首节点崩溃修复：input→output 两节点 completed、node_results 携带 duration（毫秒）；
2. 持久化修复：node_results 含 datetime（started_at/completed_at），model_dump()
   python 模式的 datetime 对象写 JSON 列会 StatementError（Object of type datetime
   is not JSON serializable）→ 端点 model_dump(mode="json") 后可落库、可响应。

变异判别：
- 端点还原 model_dump()（python 模式）→ 提交阶段 StatementError → 本用例 500 红；
- 删除 NodeResult.duration → in1 崩溃为 failed、output.success=False → 断言红。
"""
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


@pytest.mark.asyncio
async def test_execute_minimal_workflow_persists_durations():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app
    from app.models import Base
    from app.models.workflow import Workflow, WorkflowExecution

    db_engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as s:
        wf = Workflow(name="issue276-min", flow_data=_MIN_FLOW, created_by=1, organization_id=1)
        s.add(wf)
        await s.commit()
        wf_id = wf.id

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _make_user()
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/workflows/{wf_id}/execute",
                params={"stream": "false"},
                json={"text": "你好"},
            )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200, r.text[:500]
    data = r.json()["data"]
    assert data["output"]["success"] is True
    by_id = {nr["node_id"]: nr for nr in data["node_results"]}
    assert by_id["in1"]["status"] == "completed"
    assert by_id["out1"]["status"] == "completed"
    assert isinstance(by_id["in1"]["duration"], int)
    assert by_id["in1"]["duration"] >= 0

    # 落库复核：execution 行真写了 node_results（JSON 列）且带 duration
    async with factory() as s:
        ex = (await s.execute(select(WorkflowExecution))).scalars().one()
    assert ex.status == "completed"
    assert ex.error_message is None
    stored = {nr["node_id"]: nr for nr in ex.node_results}
    assert stored["in1"]["status"] == "completed"
    assert isinstance(stored["in1"]["duration"], int)

    await db_engine.dispose()
