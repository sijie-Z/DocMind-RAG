"""批次八 · 会话树 API 契约：tree / branch-context / branch-label / summary / share。

判别性：
- tree 返回 3 级分支与按分支工件统计；跨组织/跨用户 404（不泄露存在性）；
- branch-context 原位切换不创建会话（调用前后会话数不变）；
- PUT 标签后 tree 读回新标签；
- fake 摘要确定性；空分支如实无摘要；LLM 不可用 → 503 且无摘要；
- 树分享复用既有 chat 分享 token：公开 ``/api/v1/chat/shares/{token}`` 能读到
  根会话快照；再次分享轮换旧 token；他人组织 404。
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.artifact import AgentArtifact
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType

USER_ID = 1
ORG_ID = 1


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'session_tree_api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def client(db_factory):
    from app.main import app

    user = SimpleNamespace(
        id=USER_ID,
        organization_id=ORG_ID,
        token_organization_id=None,
        is_active=True,
        is_superuser=False,
        role="user",
    )

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
    title: str = "分支会话",
    parent_session_id: str | None = None,
    root_session_id: str | None = None,
    fork_point_message_id: str | None = None,
) -> ChatSession:
    session = ChatSession(
        id=sid,
        user_id=user_id,
        organization_id=org_id,
        title=title,
        status=ChatSessionStatus.ACTIVE,
        parent_session_id=parent_session_id,
        root_session_id=root_session_id or sid,
        fork_point_message_id=fork_point_message_id,
    )
    db.add(session)
    await db.flush()
    return session


async def _mk_msg(
    db, sid: str, seq: int, mtype: MessageType, content: str, *, mid: str | None = None
) -> ChatMessage:
    msg = ChatMessage(
        id=mid or str(uuid.uuid4()),
        session_id=sid,
        seq=seq,
        content=content,
        message_type=mtype,
    )
    db.add(msg)
    await db.flush()
    return msg


async def _mk_artifact(db, aid: str, conversation_id: str, *, version_no: int = 1):
    row = AgentArtifact(
        id=aid,
        organization_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation_id,
        kind="markdown",
        title=f"artifact {aid}",
        mime_type="text/markdown",
        content_sha256=hashlib.sha256(aid.encode("utf-8")).hexdigest(),
        file_size=3,
        version_no=version_no,
        status="ready",
    )
    db.add(row)
    await db.flush()
    return row


async def _seed_tree(factory):
    async with factory() as db:
        await _mk_session(db, "t-root", title="根会话")
        await _mk_session(
            db,
            "t-child",
            title="子分支",
            parent_session_id="t-root",
            root_session_id="t-root",
            fork_point_message_id="fp-root",
        )
        await _mk_session(
            db,
            "t-grand",
            title="孙分支",
            parent_session_id="t-child",
            root_session_id="t-root",
            fork_point_message_id="fp-child",
        )
        await _mk_session(db, "t-empty", title="空分支")
        await _mk_session(db, "t-foreign-org", title="外组织", org_id=ORG_ID + 1)
        await _mk_session(db, "t-foreign-user", title="他人", user_id=USER_ID + 1)
        await _mk_msg(db, "t-root", 1, MessageType.USER, "根问题", mid="fp-root")
        await _mk_msg(db, "t-root", 2, MessageType.ASSISTANT, "根回答")
        await _mk_msg(db, "t-child", 1, MessageType.USER, "子问题", mid="fp-child")
        await _mk_msg(db, "t-child", 2, MessageType.ASSISTANT, "子回答")
        await _mk_msg(db, "t-grand", 1, MessageType.USER, "孙问题")
        await _mk_artifact(db, "a-child-v1", "t-child", version_no=1)
        await _mk_artifact(db, "a-child-v2", "t-child", version_no=2)
        await db.commit()


@pytest.mark.asyncio
async def test_tree_contract_and_tenant_404(client, db_factory):
    await _seed_tree(db_factory)

    r = client.get("/api/v1/sessions/t-grand/tree")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["root_session_id"] == "t-root"
    assert data["current_session_id"] == "t-grand"
    assert data["branch_count"] == 3
    branches = {node["session_id"]: node for node in data["branches"]}
    assert list(branches) == ["t-root", "t-child", "t-grand"]
    assert branches["t-child"]["fork_point_message_id"] == "fp-root"
    assert branches["t-child"]["depth"] == 1
    assert branches["t-grand"]["depth"] == 2
    assert branches["t-grand"]["is_current"] is True
    assert branches["t-child"]["artifact_count"] == 2
    assert branches["t-child"]["artifact_latest_version"] == 2

    for sid in ("t-foreign-org", "t-foreign-user", "missing"):
        r = client.get(f"/api/v1/sessions/{sid}/tree")
        assert r.status_code == 404, f"{sid} 必须 404（不泄露归属）"
        assert r.json()["error_code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_branch_context_switch_does_not_create_session(client, db_factory):
    await _seed_tree(db_factory)
    async with db_factory() as db:
        before = (await db.execute(select(func.count(ChatSession.id)))).scalar_one()

    r = client.get("/api/v1/sessions/t-child/branch-context")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["session_id"] == "t-child"
    assert [m["content"] for m in data["messages"]] == ["子问题", "子回答"]
    assert data["artifact_count"] == 2
    assert {a["id"] for a in data["artifacts"]} == {"a-child-v1", "a-child-v2"}

    async with db_factory() as db:
        after = (await db.execute(select(func.count(ChatSession.id)))).scalar_one()
        fork_like = (
            await db.execute(
                select(func.count(ChatSession.id)).where(ChatSession.parent_session_id == "t-child")
            )
        ).scalar_one()
    assert after == before, "切换分支不得创建会话（切视图 ≠ fork）"
    assert fork_like == 1, "branch-context 不得隐式 fork（只允许种子里的 t-grand）"


@pytest.mark.asyncio
async def test_branch_label_put_reflected_in_tree(client, db_factory):
    await _seed_tree(db_factory)

    r = client.put("/api/v1/sessions/t-child/branch-label", json={"branch_label": "  试验分支  "})
    assert r.status_code == 200
    assert r.json()["data"]["branch_label"] == "试验分支"

    tree = client.get("/api/v1/sessions/t-root/tree").json()["data"]
    branch = next(n for n in tree["branches"] if n["session_id"] == "t-child")
    assert branch["branch_label"] == "试验分支"

    # 清除标签
    r = client.put("/api/v1/sessions/t-child/branch-label", json={"branch_label": "  "})
    assert r.json()["data"]["branch_label"] is None

    # Pydantic 层就拒绝超长标签（422），不会写库。
    r = client.put("/api/v1/sessions/t-child/branch-label", json={"branch_label": "x" * 101})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_summary_fake_deterministic_and_empty_branch(client, db_factory):
    await _seed_tree(db_factory)

    first = client.post("/api/v1/sessions/t-child/summary", json={"mode": "fake"})
    second = client.post("/api/v1/sessions/t-child/summary", json={"mode": "fake"})
    assert first.status_code == 200 and second.status_code == 200
    data = first.json()["data"]
    assert data["generated"] is True
    assert "子问题" in data["summary"]
    assert data["summary_model"] == "fake"
    assert second.json()["data"]["summary"] == data["summary"]

    empty = client.post("/api/v1/sessions/t-empty/summary", json={"mode": "fake"})
    assert empty.status_code == 200
    empty_data = empty.json()["data"]
    assert empty_data["generated"] is False
    assert empty_data["summary"] is None
    assert empty_data["summary_model"] is None

    bad = client.post("/api/v1/sessions/t-child/summary", json={"mode": "bogus"})
    assert bad.status_code == 400
    assert bad.json()["error_code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_llm_summary_unavailable_is_503(client, db_factory, monkeypatch):
    await _seed_tree(db_factory)
    from app.services import llm_settings_service

    async def _no_client(_db, user=None, *, user_id=None):
        return SimpleNamespace(client=None, model="unavailable", source="server", has_api_key=False)

    monkeypatch.setattr(llm_settings_service, "resolve_for_user", _no_client)

    r = client.post("/api/v1/sessions/t-child/summary", json={"mode": "llm"})
    assert r.status_code == 503
    body = r.json()
    assert body["error_code"] == "SUMMARY_MODEL_UNAVAILABLE"
    assert body["success"] is False

    tree = client.get("/api/v1/sessions/t-child/tree").json()["data"]
    branch = next(n for n in tree["branches"] if n["session_id"] == "t-child")
    assert branch["branch_summary"] is None, "失败不得落 fake 摘要"


@pytest.mark.asyncio
async def test_tree_share_reuses_chat_share_token_and_tenant_404(client, db_factory):
    await _seed_tree(db_factory)

    r = client.get("/api/v1/sessions/tree/t-root/share")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["root_session_id"] == "t-root"
    token = data["token"]
    assert data["url"] == f"/share/{token}"
    assert token

    # 既有公开只读端点即可读取根会话快照：证明没有第二套 token/鉴权。
    public = client.get(f"/api/v1/chat/shares/{token}")
    assert public.status_code == 200
    snapshot = public.json()["data"]
    assert snapshot["id"] == "t-root"
    assert [m["content"] for m in snapshot["messages"]] == ["根问题", "根回答"]

    # 再次调用轮换 token，旧链接立即失效。
    rotated = client.get("/api/v1/sessions/tree/t-root/share").json()["data"]["token"]
    assert rotated != token
    assert client.get(f"/api/v1/chat/shares/{token}").status_code == 404
    assert client.get(f"/api/v1/chat/shares/{rotated}").status_code == 200

    # 他人组织/他人用户一律 404；不泄露存在性。
    for sid in ("t-foreign-org", "t-foreign-user", "missing"):
        r = client.get(f"/api/v1/sessions/tree/{sid}/share")
        assert r.status_code == 404
        assert r.json()["error_code"] == "NOT_FOUND"
