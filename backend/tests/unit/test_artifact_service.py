"""工件服务：组织隔离 / 内容哈希 / 版本链 / 上限 / 幂等 / 对象名（真实 SQLite + 假 MinIO）。

判别点（每个用例标题写出变异点）：
- 跨组织读取一律 404（get_artifact/list 去掉 org 条件 → 红）；
- 跨用户读取一律 404（去掉 user 条件 → 红）；
- 内容哈希稳定（同内容 → 同 sha256；改一个字节 → 不同）；
- 版本链：同标题同作用域新内容 v2、v3，``replaces_artifact_id`` 指向上一版；
- 同内容重复创建幂等（不刷版本号）；同 (task, call) 重复落账返回既有行；
- 超限（正文/标题）显式 ValidationError，不静默截断；
- 对象名确定性且被约束在 ``artifacts/{org}/`` 前缀内（路径注入防护）；
- 存储故障 → ServiceUnavailableError（不吞成 200）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import NotFoundError, ServiceUnavailableError, ValidationError
from app.models.artifact import AgentArtifact
from app.models.organization import Organization
from app.services import artifact_service

ORG = 11
OTHER_ORG = 22
USER = 101
OTHER_USER = 202


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-service.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=USER))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=OTHER_USER))
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    """假 MinIO：记录 put 的 (object_name, bytes)，get 返回存入的字节。"""
    store: dict[str, bytes] = {}

    def _put(object_name, data, length, content_type="application/octet-stream", **kwargs):
        store[object_name] = data.read()
        return object_name

    class _Response:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def close(self):
            return None

        def release_conn(self):
            return None

    def _get(object_name):
        if object_name not in store:
            raise KeyError(object_name)
        return _Response(store[object_name])

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _put)
    monkeypatch.setattr(artifact_service.minio_client, "get_object", _get)
    return store


async def _create(factory, **overrides):
    kwargs = {
        "organization_id": ORG,
        "user_id": USER,
        "kind": "markdown",
        "title": "报告",
        "content": "# 第一版",
    }
    kwargs.update(overrides)
    async with factory() as db:
        row, created = await artifact_service.create_artifact(db, **kwargs)
        await db.commit()
        await db.refresh(row)
        return row, created


async def test_content_hash_is_stable_and_kind_normalized(factory, fake_storage):
    row, created = await _create(factory, content="# 内容", kind="MARKDOWN")
    assert created is True
    assert row.kind == "markdown"
    assert row.content_sha256 == artifact_service.content_sha256("# 内容")
    assert row.mime_type == "text/markdown; charset=utf-8"
    assert row.filename == "报告.md"
    assert row.status == "ready"
    assert row.version_no == 1
    assert row.replaces_artifact_id is None

    row2, created2 = await _create(factory, content="# 内容改", kind="markdown")
    assert created2 is True
    assert row2.content_sha256 != row.content_sha256, "改内容必须换哈希"


async def test_same_content_is_idempotent_no_new_version(factory):
    first, created1 = await _create(factory, content="same")
    second, created2 = await _create(factory, content="same")
    assert created1 is True and created2 is False
    assert second.id == first.id, "同链头同内容必须复用既有行，不得制造空版本"
    async with factory() as db:
        rows = (await db.execute(__import__("sqlalchemy").select(AgentArtifact))).scalars().all()
    assert len(rows) == 1


async def test_version_chain_appends_and_links(factory):
    v1, _ = await _create(factory, content="第一版")
    v2, created2 = await _create(factory, content="第二版")
    v3, _ = await _create(factory, content="第三版")
    assert created2 is True
    assert (v1.version_no, v2.version_no, v3.version_no) == (1, 2, 3)
    assert v2.replaces_artifact_id == v1.id
    assert v3.replaces_artifact_id == v2.id

    async with factory() as db:
        chain = await artifact_service.version_history(
            db, artifact=v3, organization_id=ORG, user_id=USER
        )
    assert [row.id for row in chain] == [v1.id, v2.id, v3.id], "链必须旧 → 新完整返回"

    # 从中间行读取也给完整链（含其后的新版本）。
    async with factory() as db:
        mid_chain = await artifact_service.version_history(
            db, artifact=v2, organization_id=ORG, user_id=USER
        )
    assert [row.id for row in mid_chain] == [v1.id, v2.id, v3.id]


async def test_version_chain_survives_across_runs_in_same_conversation(factory):
    """task_id 是每次运行的记账 id；同一会话复用标题必须继续成链，而不是回到 v1。"""
    v1, _ = await _create(
        factory, title="周报", content="第一版", conversation_id="conv-9", task_id="task-A"
    )
    v2, created = await _create(
        factory, title="周报", content="第二版", conversation_id="conv-9", task_id="task-B"
    )
    assert created is True
    assert v2.version_no == 2, "跨 run 的同一会话标题必须追加版本"
    assert v2.replaces_artifact_id == v1.id
    # 读侧同口径：从新版本回溯必须能看到上一版本，否则版本历史退化成 1 条。
    async with factory() as db:
        chain = await artifact_service.version_history(
            db, artifact=v2, organization_id=ORG, user_id=USER
        )
    assert [row.version_no for row in chain] == [1, 2]


async def test_different_title_or_scope_is_a_separate_chain(factory):
    a, _ = await _create(factory, title="A", content="1")
    b, _ = await _create(factory, title="B", content="1")
    c, _ = await _create(factory, title="A", content="2", conversation_id="conv-1")
    assert b.replaces_artifact_id is None, "不同标题不得串成一条链"
    assert c.replaces_artifact_id is None, "不同会话作用域不得串成一条链"
    assert a.version_no == b.version_no == c.version_no == 1


async def test_task_call_dedup_returns_existing_row(factory):
    first, created1 = await _create(
        factory, content="调用产出", task_id="task-1", source_call_id="call-1"
    )
    second, created2 = await _create(
        factory, content="调用产出", task_id="task-1", source_call_id="call-1"
    )
    assert created1 is True and created2 is False
    assert second.id == first.id

    async with factory() as db:
        found = await artifact_service.artifacts_for_call(
            db, task_id="task-1", source_call_id="call-1"
        )
    assert found is not None and found.id == first.id


async def test_org_and_user_isolation(factory):
    row, _ = await _create(factory)
    async with factory() as db:
        with pytest.raises(NotFoundError):
            await artifact_service.get_artifact(
                db, artifact_id=row.id, organization_id=OTHER_ORG, user_id=USER
            )
        # 同组织不同用户：同样视为不存在（用户级隔离）。
        with pytest.raises(NotFoundError):
            await artifact_service.get_artifact(
                db, artifact_id=row.id, organization_id=ORG, user_id=OTHER_USER
            )
        # 超管平台口径（user_id=None）可读。
        ok = await artifact_service.get_artifact(
            db, artifact_id=row.id, organization_id=ORG, user_id=None
        )
        assert ok.id == row.id

        cross = await artifact_service.list_artifacts(db, organization_id=OTHER_ORG, user_id=USER)
        assert cross == [], "跨组织列表不得泄露任何行"
        own = await artifact_service.list_artifacts(db, organization_id=ORG, user_id=USER)
        assert [item.id for item in own] == [row.id]


async def test_size_and_title_caps_raise_validation(factory):
    async with factory() as db:
        with pytest.raises(ValidationError) as exc:
            await artifact_service.create_artifact(
                db,
                organization_id=ORG,
                user_id=USER,
                kind="text",
                title="x",
                content="a" * (artifact_service.MAX_CONTENT_BYTES + 1),
            )
        assert exc.value.error_code == "VALIDATION_ERROR"
        with pytest.raises(ValidationError):
            await artifact_service.create_artifact(
                db,
                organization_id=ORG,
                user_id=USER,
                kind="text",
                title="t" * (artifact_service.MAX_TITLE_CHARS + 1),
                content="ok",
            )
        with pytest.raises(ValidationError):
            await artifact_service.create_artifact(
                db,
                organization_id=ORG,
                user_id=USER,
                kind="exe",
                title="t",
                content="ok",
            )
        with pytest.raises(ValidationError):
            await artifact_service.create_artifact(
                db,
                organization_id=ORG,
                user_id=USER,
                kind="text",
                title="t",
                content="",
            )


async def test_object_name_is_deterministic_and_scoped(factory, fake_storage):
    row, _ = await _create(factory, content="对象名")
    expected = artifact_service.object_name_for(ORG, row.content_sha256, "markdown")
    assert row.storage_object_name == expected
    assert expected.startswith(f"artifacts/{ORG}/")
    assert row.content_sha256 in expected
    assert expected.endswith(".md")
    assert fake_storage[expected] == "对象名".encode()


async def test_object_name_rejects_path_injection():
    sha = "a" * 64
    with pytest.raises(ValueError):
        artifact_service.object_name_for(ORG, "../../etc/passwd", "text")
    # 组织段被白名单清洗（数字转字符串后再清洗），不会带出路径分隔符。
    name = artifact_service.object_name_for(ORG, sha, "text")
    assert name.count("/") == 3
    assert ".." not in name


async def test_storage_failure_is_service_unavailable(factory, monkeypatch):
    def _boom(*args, **kwargs):
        raise ConnectionError("minio down")

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _boom)
    async with factory() as db:
        with pytest.raises(ServiceUnavailableError) as exc:
            await artifact_service.create_artifact(
                db,
                organization_id=ORG,
                user_id=USER,
                kind="text",
                title="t",
                content="c",
            )
    assert exc.value.error_code == "ARTIFACT_STORAGE_UNAVAILABLE"
    async with factory() as db:
        rows = (await db.execute(__import__("sqlalchemy").select(AgentArtifact))).scalars().all()
    assert rows == [], "存储失败不得留下指向缺失内容的行"


async def test_parse_artifact_payload_channels():
    from types import SimpleNamespace

    payload = {"kind": "artifact/v1", "artifact_kind": "markdown", "title": "T", "content": "C"}
    meta = SimpleNamespace(extra={"artifact": payload})
    result = SimpleNamespace(meta=meta, data=None)
    parsed = artifact_service.parse_artifact_payload(result)
    assert parsed is not None and parsed["kind"] == "markdown" and parsed["content"] == "C"

    # 字符串通道：顶层 kind 必须是标记值，真实类型在 artifact_kind。
    import json

    text = json.dumps(payload)
    parsed_str = artifact_service.parse_artifact_payload(text)
    assert parsed_str is not None and parsed_str["kind"] == "markdown"

    # 普通长文本不得被误判为工件。
    assert artifact_service.parse_artifact_payload("# 普通 Markdown 报告\n" * 50) is None
    assert artifact_service.parse_artifact_payload('{"hello": "world"}') is None


async def test_read_content_roundtrip(factory):
    row, _ = await _create(factory, content="读回内容", kind="json")
    payload = await artifact_service.read_content(row)
    assert payload.decode("utf-8") == "读回内容"
