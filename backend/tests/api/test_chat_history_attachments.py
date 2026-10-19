"""T3 回归测试 —— 聊天历史附件回填（GET /api/v1/chat/conversations/{id}）。

修复前症状
==========
1. 端点在遍历历史消息时把 **ChatSession ORM 对象**当作 AsyncSession 使用
   （``await session.execute(select(Document)...)``）——必抛 ``AttributeError``；
   该异常又被逐消息的宽 ``except Exception`` 只记 warning 吞掉。只要历史 USER
   消息带 ``meta_data.file_ids``，附件回填必然失败：响应里 ``files`` 永远缺失，
   前端历史会话的附件 chips 恢复不出来。
2. 该 Document 查询没有组织条件 —— 客户端自填的 ``meta.file_ids``（发送时原样
   落库，见 chat_service.run_rag_pipeline）可让端点回读**其他组织**文档的文件名，
   构成跨租户泄露面。

测试替身说明
============
用 ``TestClient`` + ``dependency_overrides``（本目录约定，不连真实服务）。假的
AsyncSession 只回答本端点用到的三类 SELECT，其中 Document 查询按**编译后的绑定
参数**模拟数据库语义（``id IN (...)`` 与可选的 ``organization_id = ...``），
因此断言的是端点真实生成的查询条件，而不是 mock 的调用顺序。

变异点（判别力说明，每个用例 docstring 内亦有标注）
====================================================
- 用例 1 ``test_history_user_message_restores_attachment_files``：
  把 ``db.execute`` 改回 ``session.execute`` → 红（响应缺 ``files``）。
- 用例 2 ``test_cross_org_document_ids_are_not_exposed``：
  去掉 ``Document.organization_id == <effective org>`` 条件（只保留 id IN）
  → 红（他人组织文档的文件名出现在响应里）。
- 用例 3 ``test_metadata_parse_failure_is_logged_with_traceback``：
  把逐消息 ``logger.exception`` 改回 ``logger.warning`` → 红（无 ERROR 级堆栈
  留痕）；同时验证单条坏消息不拖垮其它消息的附件回填。
- 用例 4 ``test_org_less_caller_gets_no_attachment_enrichment``：
  去掉组织解析失败时的 fail-closed（无组织上下文仍执行 Document 查询）→ 红
  （无组织用户的响应里出现附件文件名；该用例同时显式断言未发起 Document 查询）。
- 用例 5 ``test_token_organization_fallback_filters_by_token_org``：
  忽略 ``token_organization_id``（改用 ``current_user.organization_id``）→ 红
  （token 组织内的文档回填被错误跳过）。
- 用例 6 ``test_org_less_superuser_uses_resolved_real_org``：
  把无组织超管的回退组织写死为 1 号组织（issue #168 旧行为）→ 红
  （1 号组织的文档混入响应）。
"""
import json
import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, ChatSession, MessageType
from app.models.document import Document
from app.models.organization import Organization


class _Result:
    """最小 Result 替身：支持 scalars().all() 与 scalar_one_or_none()。"""

    def __init__(self, *, rows=None, one=None):
        self._rows = rows if rows is not None else []
        self._one = one

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._one


class _FakeDb:
    """只回答 get_conversation 用到的 SELECT；其余语句直接炸。

    ``resolved_org_id`` 供无组织超管场景回答 ``select(Organization.id)``
    （require_organization 的「回源真实组织」路径）。
    """

    def __init__(self, *, session, messages, documents, resolved_org_id=None):
        self._session = session
        self._messages = messages
        self._documents = documents
        self._resolved_org_id = resolved_org_id
        self.queries = []

    async def execute(self, stmt):
        self.queries.append(stmt)
        entity = stmt.column_descriptions[0].get("entity")
        if entity is ChatSession:
            return _Result(one=self._session)
        if entity is ChatMessage:
            return _Result(rows=self._messages)
        if entity is Document:
            return _Result(rows=self._filter_documents(stmt))
        if entity is Organization:
            return _Result(one=self._resolved_org_id)
        raise AssertionError(f"get_conversation 不应执行其它查询: {stmt}")

    def _filter_documents(self, stmt):
        """按编译后的绑定参数模拟数据库过滤（id IN / organization_id =）。"""
        params = stmt.compile().params
        wanted_ids: set[str] = set()
        wanted_orgs: set = set()
        for key, value in params.items():
            if key.startswith("id_"):
                if isinstance(value, (list, tuple, set)):
                    wanted_ids.update(str(v) for v in value)
                else:
                    wanted_ids.add(str(value))
            elif key.startswith("organization_id"):
                wanted_orgs.add(value)
        rows = [d for d in self._documents if str(d.id) in wanted_ids]
        if wanted_orgs:
            rows = [d for d in rows if d.organization_id in wanted_orgs]
        return rows


def _make_user(*, organization_id=1, user_id=1):
    user = MagicMock()
    user.id = user_id
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = organization_id
    user.is_superuser = False
    user.is_active = True
    # 真实 User 模型没有该属性（get_user_org_id 的兜底取 None）
    user.token_organization_id = None
    return user


def _make_session(**overrides):
    defaults = dict(
        id="conv-1",
        user_id=1,
        organization_id=1,
        title="历史会话",
        settings={},
    )
    defaults.update(overrides)
    return ChatSession(**defaults)


def _make_message(*, message_id, meta_data, message_type=MessageType.USER):
    return ChatMessage(
        id=message_id,
        session_id="conv-1",
        content="消息内容",
        message_type=message_type,
        meta_data=json.dumps(meta_data) if isinstance(meta_data, dict) else meta_data,
        feedback=0,
        feedback_note=None,
    )


def _make_document(*, doc_id, filename, organization_id, uploaded_by=1):
    return Document(
        id=doc_id,
        filename=filename,
        title=filename,
        organization_id=organization_id,
        uploaded_by=uploaded_by,
    )


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def _install_overrides(app, *, db, user):
    async def _override_db():
        yield db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user


def _clear_overrides(app):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _get_conversation(client, db, user, conversation_id="conv-1"):
    from app.main import app

    _install_overrides(app, db=db, user=user)
    try:
        return client.get(
            f"/api/v1/chat/conversations/{conversation_id}",
            headers={"Authorization": "Bearer test_token"},
        )
    finally:
        _clear_overrides(app)


class TestHistoryAttachmentBackfill:
    def test_history_user_message_restores_attachment_files(self, client: TestClient):
        """历史 USER 消息的 file_ids 指向本组织文档 → 详情接口返回文件名。

        变异点：``db.execute`` 改回 ``session.execute``（ChatSession 没有
        execute 方法 → AttributeError 被逐消息 except 吞掉）→ ``files`` 缺失，
        本用例红。这正是修复前的生产行为。
        """
        session = _make_session()
        message = _make_message(message_id="msg-1", meta_data={"file_ids": ["doc-own"]})
        doc = _make_document(doc_id="doc-own", filename="own-report.pdf", organization_id=1)
        db = _FakeDb(session=session, messages=[message], documents=[doc])

        resp = _get_conversation(client, db, _make_user(organization_id=1))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        msg = body["data"]["messages"][0]
        assert msg["files"] == [{"id": "doc-own", "name": "own-report.pdf", "status": "done"}], (
            f"历史附件未回填（session/db 混用回归？）: {msg}"
        )

    def test_cross_org_document_ids_are_not_exposed(self, client: TestClient):
        """file_ids 混入他人组织文档 id → 该文档不得出现在响应中。

        变异点：去掉 Document 查询的 ``organization_id`` 条件（只保留
        ``id IN (...)``）→ 他人组织的文件名出现在响应里 → 本用例红。
        """
        session = _make_session()
        message = _make_message(
            message_id="msg-1", meta_data={"file_ids": ["doc-own", "doc-foreign"]}
        )
        own = _make_document(doc_id="doc-own", filename="own-report.pdf", organization_id=1)
        foreign = _make_document(
            doc_id="doc-foreign", filename="other-tenant-secret.pdf", organization_id=2
        )
        db = _FakeDb(session=session, messages=[message], documents=[own, foreign])

        resp = _get_conversation(client, db, _make_user(organization_id=1))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        files = body["data"]["messages"][0].get("files", [])
        assert [f["id"] for f in files] == ["doc-own"], f"跨组织文档被回显: {files}"
        assert "other-tenant-secret.pdf" not in resp.text

    def test_metadata_parse_failure_is_logged_with_traceback(
        self, client: TestClient, caplog
    ):
        """坏元数据只降级单条消息，但必须留下 ERROR 级堆栈（不得静默）。

        变异点：逐消息 ``logger.exception`` 改回 ``logger.warning`` →
        caplog 中无 ERROR 记录/无 exc_info → 本用例红。（修复前 AttributeError
        正是被这条 warning 吞掉的。）
        """
        session = _make_session()
        bad = _make_message(message_id="msg-bad", meta_data='{"file_ids": [broken')
        good = _make_message(message_id="msg-good", meta_data={"file_ids": ["doc-own"]})
        doc = _make_document(doc_id="doc-own", filename="own-report.pdf", organization_id=1)
        db = _FakeDb(session=session, messages=[bad, good], documents=[doc])

        with caplog.at_level(logging.ERROR, logger="app.api.v1.endpoints.chat"):
            resp = _get_conversation(client, db, _make_user(organization_id=1))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        messages = body["data"]["messages"]
        assert len(messages) == 2, "单条坏元数据不得拖垮整段历史"
        # 隔离性：坏消息之后的正常消息仍能回填附件
        assert messages[1]["files"] == [
            {"id": "doc-own", "name": "own-report.pdf", "status": "done"}
        ]

        error_records = [
            r
            for r in caplog.records
            if r.name == "app.api.v1.endpoints.chat" and r.levelno >= logging.ERROR
        ]
        assert error_records, "坏元数据解析失败必须留下 ERROR 级日志（不得只 warning）"
        assert any(r.exc_info for r in error_records), "ERROR 日志必须携带堆栈（logger.exception）"

    def test_org_less_caller_gets_no_attachment_enrichment(self, client: TestClient):
        """无组织上下文 → 附件回填 fail closed（历史消息本身仍可读）。

        变异点：去掉组织解析失败时的 fail-closed 分支（无组织仍执行 Document
        查询）→ 该用例红。判别力由两条断言共同保证：响应 ``files == []``，且
        显式断言未发起 Document 查询。（假 DB 无法模拟 SQL 的 ``IS NULL`` 语义
        ——真实 DB 下 ``organization_id == None`` 只会命中组织为 NULL 的历史
        脏数据行；即便真实 DB 不回显，这条查询本身也违背 fail-closed 口径。）
        """
        session = _make_session(organization_id=None)
        message = _make_message(message_id="msg-1", meta_data={"file_ids": ["doc-own"]})
        doc = _make_document(doc_id="doc-own", filename="own-report.pdf", organization_id=1)
        db = _FakeDb(session=session, messages=[message], documents=[doc])

        resp = _get_conversation(client, db, _make_user(organization_id=None))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, "无组织不应连历史会话本身都无法读取"
        assert body["data"]["messages"][0].get("files") == [], (
            "无组织上下文必须 fail closed：不得回填任何附件"
        )
        assert not any(
            stmt.column_descriptions[0].get("entity") is Document for stmt in db.queries
        ), "无组织上下文不得发起 Document 查询"

    def test_token_organization_fallback_filters_by_token_org(self, client: TestClient):
        """有效组织可由 token 组织上下文提供（user.organization_id 为空）。

        变异点：忽略 ``token_organization_id``（改用 ``current_user.organization_id``）
        → 附件回填被 fail-closed 跳过（files 为空）或按错误组织过滤 → 本用例红。
        """
        user = _make_user(organization_id=None)
        user.token_organization_id = 42
        session = _make_session(organization_id=42)
        message = _make_message(
            message_id="msg-1", meta_data={"file_ids": ["doc-token", "doc-other"]}
        )
        token_doc = _make_document(
            doc_id="doc-token", filename="token-org.pdf", organization_id=42
        )
        other_doc = _make_document(
            doc_id="doc-other", filename="other-org.pdf", organization_id=1
        )
        db = _FakeDb(session=session, messages=[message], documents=[token_doc, other_doc])

        resp = _get_conversation(client, db, user)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        files = body["data"]["messages"][0].get("files", [])
        assert [f["id"] for f in files] == ["doc-token"], f"token 组织解析错误: {files}"
        assert "other-org.pdf" not in resp.text

    def test_org_less_superuser_uses_resolved_real_org(self, client: TestClient):
        """无组织超管 → require_organization 回源库中真实组织，附件按该组织过滤。

        变异点：把回退组织写死为 1 号组织（issue #168 的旧行为）→ 1 号组织的
        文档混入响应 → 本用例红。
        """
        user = _make_user(organization_id=None)
        user.is_superuser = True
        session = _make_session(organization_id=None)
        message = _make_message(
            message_id="msg-1", meta_data={"file_ids": ["doc-real", "doc-org1"]}
        )
        real_doc = _make_document(
            doc_id="doc-real", filename="real-org.pdf", organization_id=3
        )
        org1_doc = _make_document(doc_id="doc-org1", filename="org-1.pdf", organization_id=1)
        db = _FakeDb(
            session=session,
            messages=[message],
            documents=[real_doc, org1_doc],
            resolved_org_id=3,
        )

        resp = _get_conversation(client, db, user)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        files = body["data"]["messages"][0].get("files", [])
        assert [f["id"] for f in files] == ["doc-real"], f"超管回源组织错误: {files}"
        assert "org-1.pdf" not in resp.text
