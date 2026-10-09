"""issue #301 批 A（recon 表一 #5）：工作流节点失败不得再报 success/completed。

复现（修复前，真引擎 + 真库）：
- 两节点工作流：n0 是 api_call（URL 协议非法 → ValueError 确定性失败，零网络
  依赖），out1 是 output（正常完成）；
- 端点非流式：响应 `success: true`、执行记录 `status="completed"`、
  error_message=None；流式：complete 事件载荷 `success: true`、记录同样
  completed——节点级失败只藏在 node_results / node_error 事件里。

修复语义：execute() 汇总 node_results（任一 failed → 总体 success=False +
error 摘要）；端点按 result["success"] 如实落 status（failed + error_message）
与响应 success；流式 complete 载荷如实携带 success=False；node_error 事件
保持如实（不变）。

变异判别（每条精确红）：
- M5a 引擎 execute() 还原无条件 `"success": True` → 用例 1/2 红；
- M5b 端点非流式还原无条件 `completed` + `"success": True` → 用例 1 红；
- M5c 端点流式 workflow_result 分支还原无条件 completed → 用例 2 红。
"""
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# n0 确定性失败（ftp 协议被 SSRF 守卫拒绝，毫秒级、无网络）；out1 正常收尾。
_FAIL_NODE_FLOW = {
    "nodes": [
        {"id": "n0", "type": "api_call",
         "data": {"url": "ftp://example.com/data", "method": "GET"},
         "position": {"x": 0, "y": 0}},
        {"id": "out1", "type": "output", "data": {}, "position": {"x": 1, "y": 0}},
    ],
    "edges": [{"id": "e1", "source": "n0", "target": "out1"}],
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


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
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
        wf = Workflow(name="issue301-node-fail", flow_data=flow_data, created_by=1)
        s.add(wf)
        await s.commit()
        wf_id = wf.id

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _make_user()
    return app, db_engine, factory, wf_id


async def _request(factory, wf_id: int, *, stream: bool) -> httpx.Response:
    from app.main import app

    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as ac:
            return await ac.post(
                f"/api/v1/workflows/{wf_id}/execute",
                params={"stream": "true" if stream else "false"},
                json={"text": "你好"},
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_nonstream_node_failure_reports_failure_and_marks_record_failed():
    """非流式：节点失败 → 响应 success=false、记录 failed + error_message。

    修复前本用例红：响应 success=true、记录 completed、error_message=None。
    """
    from app.models.workflow import WorkflowExecution

    app, db_engine, factory, wf_id = await _setup_app(_FAIL_NODE_FLOW)
    try:
        r = await _request(factory, wf_id, stream=False)
        assert r.status_code == 200, r.text[:500]
        body = r.json()
        assert body["success"] is False, f"节点失败不得再报上层 success=true: {body}"
        data = body["data"]
        assert data["status"] == "failed"
        assert data["output"]["success"] is False
        assert "不允许的协议" in (data["output"].get("error") or ""), (
            f"总体结果必须携带失败原因摘要: {data['output']}"
        )
        by_id = {nr["node_id"]: nr for nr in data["node_results"]}
        assert by_id["n0"]["status"] == "failed"
        assert "不允许的协议" in (by_id["n0"]["error"] or "")
        assert by_id["out1"]["status"] == "completed", "后续节点照常执行（失败不阻断整图）"

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed", "执行记录必须落 failed（不再 completed）"
        assert "不允许的协议" in (ex.error_message or "")
        assert ex.completed_at is not None
        assert ex.output_data["success"] is False
    finally:
        await db_engine.dispose()


@pytest.mark.asyncio
async def test_stream_node_failure_complete_payload_honest_and_record_failed():
    """流式：node_error 如实 + complete 载荷 success=false + 记录 failed。

    修复前本用例红：complete 载荷 success=true、记录 completed。
    """
    from app.models.workflow import WorkflowExecution

    app, db_engine, factory, wf_id = await _setup_app(_FAIL_NODE_FLOW)
    try:
        r = await _request(factory, wf_id, stream=True)
        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        types = [t for t, _ in events]

        assert "node_error" in types, f"节点失败必须以 node_error 事件如实暴露: {types}"
        node_err = next(d for t, d in events if t == "node_error")
        assert node_err["node_id"] == "n0"
        assert "不允许的协议" in node_err["error"]

        # 收口形态不变（complete 是终态事件）；载荷必须如实
        assert types[-1] == "complete", types
        complete = next(d for t, d in events if t == "complete")
        assert complete["success"] is False, f"complete 载荷不得再报 success=true: {complete}"

        # 终态重放的 node_complete 里失败节点如实带 status=failed
        n0_replays = [d for t, d in events if t == "node_complete" and d.get("node_id") == "n0"]
        assert n0_replays and n0_replays[-1]["status"] == "failed"

        async with factory() as s:
            ex = (await s.execute(select(WorkflowExecution))).scalars().one()
        assert ex.status == "failed"
        assert "不允许的协议" in (ex.error_message or "")
        assert ex.completed_at is not None
        assert ex.output_data["success"] is False
    finally:
        await db_engine.dispose()
