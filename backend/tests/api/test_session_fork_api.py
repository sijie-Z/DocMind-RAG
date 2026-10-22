"""issue #476：会话 fork/lineage 的 HTTP 面契约（tests/api 惯例：假鉴权 + sqlite）。

覆盖：鉴权（401）、越权/跨组织 404（不 403、不 200）、成功契约（字段齐全）、
边界非法/运行中任务 409（error_code 可判别）、Idempotency-Key 重放、
lineage 契约、审计口径（action=session_fork，detail 含父/子/边界/checkpoint）。
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType

USER_ID = 1
ORG_ID = 1


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fork_api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def audit_calls(monkeypatch):
    from app.api.v1.endpoints import sessions as sessions_module

    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(sessions_module.audit_service, "log_activity", _capture)
    return calls


@pytest.fixture
def client(db_factory, audit_calls):
    from app.main import app

    user = SimpleNamespace(id=USER_ID, organization_id=ORG_ID, is_active=True, is_superuser=False)

    async def _override_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


async def _mk_session(
    db,
    sid: str,
    *,
    user_id: int = USER_ID,
    org_id: int | None = ORG_ID,
    title: str = "母会话",
) -> ChatSession:
    session = ChatSession(
        id=sid,
        user_id=user_id,
        organization_id=org_id,
        title=title,
        status=ChatSessionStatus.ACTIVE,
        root_session_id=sid,
    )
    db.add(session)
    await db.flush()
    return session


async def _mk_msg(db, sid: str, seq: int, mtype: MessageType, content: str) -> ChatMessage:
    msg = ChatMessage(
        id=str(uuid.uuid4()), session_id=sid, seq=seq, content=content, message_type=mtype
    )
    db.add(msg)
    await db.flush()
    return msg


async def _seed_conversation(factory, sid: str = "src-1", **session_kwargs):
    """建一个 [U1, A1] 的已结算会话，返回 (session_id, a1_id)。"""
    async with factory() as db:
        await _mk_session(db, sid, **session_kwargs)
        await _mk_msg(db, sid, 1, MessageType.USER, "问题一")
        a1 = await _mk_msg(db, sid, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()
        return sid, a1.id


# ── 鉴权 / 租户隔离 ────────────────────────────────────────────────────────


def _envelope(source_id: str) -> dict:
    """最小可用 v1 快照信封（parse_snapshot 只要求 ctx 为 dict）。"""
    from app.agent import checkpoint

    return {
        "v": checkpoint.CHECKPOINT_VERSION,
        "session_id": source_id,
        "ctx": {"task_id": "src-task-1", "query": "原问题"},
        "plan": None,
        "history": [],
        "config": {},
        "run": {},
        "partial_output": "",
        "updated_by": "after_planning",
    }


def test_fork_requires_auth(db_factory):
    from app.main import app

    r = TestClient(app).post("/api/v1/sessions/src-1/fork")
    assert r.status_code == 401, "未登录不得 fork"


@pytest.mark.asyncio
async def test_fork_foreign_user_and_cross_org_are_404(client, db_factory):
    await _seed_conversation(db_factory, "src-1")

    async with db_factory() as db:
        await _mk_session(db, "foreign-1", user_id=USER_ID + 1)
        await _mk_msg(db, "foreign-1", 1, MessageType.USER, "别人的问题")
        await _mk_msg(db, "foreign-1", 2, MessageType.ASSISTANT, "别人的回答")
        # 同一用户、另一个组织的会话（如用户迁移组织后的历史数据）
        await _mk_session(db, "other-org-1", org_id=ORG_ID + 1)
        await _mk_msg(db, "other-org-1", 1, MessageType.USER, "别的组织")
        await _mk_msg(db, "other-org-1", 2, MessageType.ASSISTANT, "别的组织回答")
        await db.commit()

    for sid in ("foreign-1", "other-org-1", "missing-session"):
        r = client.post(f"/api/v1/sessions/{sid}/fork")
        assert r.status_code == 404, f"{sid} 必须 404（不泄露归属，不返回 403）"
        assert r.json()["error_code"] == "NOT_FOUND"


# ── 成功契约 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_success_contract_and_audit(client, db_factory, audit_calls):
    sid, a1_id = await _seed_conversation(db_factory, "src-1", title="原标题")

    r = client.post(
        f"/api/v1/sessions/{sid}/fork",
        json={"message_id": a1_id, "title": "分支标题", "seed": "摘要种子", "reason": "explore"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["id"] and data["id"] != sid
    assert data["title"] == "分支标题"
    assert data["parent_session_id"] == sid
    assert data["root_session_id"] == sid
    assert data["fork_point_message_id"] == a1_id
    assert data["fork_reason"] == "explore"
    assert data["copied_message_count"] == 2
    assert data["message_count"] == 2
    assert data["boundary_seq"] == 2
    assert data["is_seeded"] is True
    assert data["checkpoint"]["cloned"] is False
    assert data["checkpoint"]["task_id"] is None
    assert data["checkpoint"]["source_task_id"] is None
    assert data["idempotent_replay"] is False

    # 审计口径：action / 父 id / 子 id / 边界消息 / 是否克隆 checkpoint
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["action"] == "session_fork"
    assert call["target_type"] == "chat_session"
    assert call["target_id"] == data["id"]
    detail = json.loads(call["detail"])
    assert detail["parent_session_id"] == sid
    assert detail["child_session_id"] == data["id"]
    assert detail["boundary_message_id"] == a1_id
    assert detail["checkpoint_cloned"] is False

    # 子会话真的落库且母会话不受影响
    async with db_factory() as db:
        child_msgs = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == data["id"])
                    .order_by(ChatMessage.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [m.content for m in child_msgs] == ["问题一", "回答一"]
        parent_msgs = (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == sid)))
            .scalars()
            .all()
        )
        assert len(parent_msgs) == 2


@pytest.mark.asyncio
async def test_fork_idempotency_key_header_replay(client, db_factory):
    sid, _ = await _seed_conversation(db_factory, "src-1")
    headers = {"Idempotency-Key": "hook-42"}

    r1 = client.post(f"/api/v1/sessions/{sid}/fork", headers=headers)
    r2 = client.post(f"/api/v1/sessions/{sid}/fork", headers=headers)

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]
    assert r2.json()["data"]["idempotent_replay"] is True

    async with db_factory() as db:
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == sid)))
            .scalars()
            .all()
        )
        assert len(children) == 1


@pytest.mark.asyncio
async def test_checkpoint_clone_contract_and_idempotent_replay(client, db_factory, audit_calls):
    """checkpoint 克隆的 HTTP 契约：cloned=true、task_id 为克隆 id、重放同 id、审计留痕。"""
    sid, a1_id = await _seed_conversation(db_factory, "src-1")
    async with db_factory() as db:
        db.add(
            AgentTask(
                id="src-task-1",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=sid,
                query="原问题",
                status=TaskStatus.COMPLETED,
                context_snapshot=_envelope(sid),
            )
        )
        await db.commit()

    headers = {"Idempotency-Key": "ckpt-key-1"}
    r1 = client.post(
        f"/api/v1/sessions/{sid}/fork",
        json={"message_id": a1_id, "include_checkpoint": True},
        headers=headers,
    )
    assert r1.status_code == 200
    data1 = r1.json()["data"]
    assert data1["checkpoint"]["cloned"] is True
    assert data1["checkpoint"]["task_id"] != "src-task-1"
    assert data1["checkpoint"]["source_task_id"] == "src-task-1"
    detail = json.loads(audit_calls[-1]["detail"])
    assert detail["checkpoint_cloned"] is True
    assert detail["checkpoint_source_task_id"] == "src-task-1"

    # 幂等重放：同一子会话 + 同一**克隆任务** id（绝不是母任务 id）
    r2 = client.post(
        f"/api/v1/sessions/{sid}/fork",
        json={"message_id": a1_id, "include_checkpoint": True},
        headers=headers,
    )
    data2 = r2.json()["data"]
    assert data2["id"] == data1["id"]
    assert data2["idempotent_replay"] is True
    assert data2["checkpoint"]["task_id"] == data1["checkpoint"]["task_id"]

    async with db_factory() as db:
        clone = await db.get(AgentTask, data1["checkpoint"]["task_id"])
        assert clone is not None
        assert clone.session_id == data1["id"]
        assert clone.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_cancel_paused_clone_task_converges(client, db_factory, monkeypatch):
    """PAUSED 克隆任务没有活跃 run：取消必须直接收敛为 CANCELLED（200），不是 202。"""
    from app.agent import checkpoint as checkpoint_module
    from app.agent import service as agent_service_module

    monkeypatch.setattr(checkpoint_module, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(agent_service_module, "AsyncSessionLocal", db_factory)
    async with db_factory() as db:
        db.add(
            AgentTask(
                id="task-paused",
                user_id=USER_ID,
                organization_id=ORG_ID,
                query="分叉快照",
                status=TaskStatus.PAUSED,
            )
        )
        await db.commit()

    r = client.post("/api/v1/agent/tasks/task-paused/cancel")
    assert r.status_code == 200
    assert r.json()["data"]["state"] == "cancelled"

    async with db_factory() as db:
        row = await db.get(AgentTask, "task-paused")
        assert row.status == TaskStatus.CANCELLED, "PAUSED 取消必须落成终态，不能停留在 PAUSED"


# ── 409 契约（边界 / 运行中任务） ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_invalid_boundary_is_409(client, db_factory):
    async with db_factory() as db:
        await _mk_session(db, "src-1")
        await _mk_msg(db, "src-1", 1, MessageType.USER, "问题一")
        mid = await _mk_msg(db, "src-1", 2, MessageType.ASSISTANT, "中间回答")
        await _mk_msg(db, "src-1", 3, MessageType.ASSISTANT, "最终回答")
        await db.commit()

    r = client.post("/api/v1/sessions/src-1/fork", json={"message_id": mid.id})
    assert r.status_code == 409
    assert r.json()["error_code"] == "FORK_BOUNDARY_INVALID"
    assert "边界" in r.json()["message"] or "轮次" in r.json()["message"]


@pytest.mark.asyncio
async def test_fork_running_task_is_409(client, db_factory):
    sid, _ = await _seed_conversation(db_factory, "src-1")
    async with db_factory() as db:
        db.add(
            AgentTask(
                id="task-live",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=sid,
                query="进行中",
                status=TaskStatus.RUNNING,
            )
        )
        await db.commit()

    r = client.post(f"/api/v1/sessions/{sid}/fork")
    assert r.status_code == 409
    assert r.json()["error_code"] == "FORK_CHECKPOINT_REQUIRED"

    r2 = client.post(f"/api/v1/sessions/{sid}/fork", json={"include_checkpoint": True})
    assert r2.status_code == 409
    assert r2.json()["error_code"] == "FORK_CHECKPOINT_UNAVAILABLE"


# ── lineage ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lineage_contract(client, db_factory):
    sid, a1_id = await _seed_conversation(db_factory, "root-1")
    r = client.post(f"/api/v1/sessions/{sid}/fork", json={"message_id": a1_id})
    child_id = r.json()["data"]["id"]

    r_child = client.get(f"/api/v1/sessions/{child_id}/lineage")
    assert r_child.status_code == 200
    data = r_child.json()["data"]
    assert data["session_id"] == child_id
    assert data["parent_session_id"] == sid
    assert data["root_session_id"] == sid
    assert data["is_fork"] is True
    assert data["depth"] == 1
    assert [a["id"] for a in data["ancestors"]] == [sid]
    assert data["children"] == []

    r_root = client.get(f"/api/v1/sessions/{sid}/lineage")
    root_data = r_root.json()["data"]
    assert root_data["is_fork"] is False
    assert root_data["depth"] == 0
    assert [c["id"] for c in root_data["children"]] == [child_id]


@pytest.mark.asyncio
async def test_lineage_cross_org_404(client, db_factory):
    async with db_factory() as db:
        await _mk_session(db, "other-org-1", org_id=ORG_ID + 1)
        await db.commit()

    r = client.get("/api/v1/sessions/other-org-1/lineage")
    assert r.status_code == 404

    r2 = client.get("/api/v1/sessions/does-not-exist/lineage")
    assert r2.status_code == 404


# ── 列表过滤（按 parent_session_id） ───────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_session_list_filters_by_parent(client, db_factory):
    sid, _ = await _seed_conversation(db_factory, "src-1")
    r = client.post(f"/api/v1/sessions/{sid}/fork")
    child_id = r.json()["data"]["id"]

    r_list = client.get("/api/v1/agent/sessions", params={"parent_session_id": sid})
    assert r_list.status_code == 200
    ids = [s["id"] for s in r_list.json()["data"]["sessions"]]
    assert ids == [child_id]
    child_item = r_list.json()["data"]["sessions"][0]
    assert child_item["parent_session_id"] == sid
    assert child_item["root_session_id"] == sid
    assert child_item["is_fork"] is True
