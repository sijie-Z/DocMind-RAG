"""Knowledge Base API 集成测试 — 知识库查询与统计。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.schemas.knowledge import SearchSuggestionResponse


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class _AsyncCtxMgr:
    """支持 async with 的 mock 上下文管理器。"""
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_mock_user(*, is_superuser=False):
    """调用方主体。

    `permission_required` 现在直接信任 `current_user`（它本身就是每次请求回源 DB
    加载的 User），不再二次 `db.get(User, ...)`。所以「这个调用方有权限」只能通过
    `current_user` 表达 —— 以前那种「`current_user` 是普通用户、但 mock 的 `db.get`
    返回超管」的搭配不再能绕过权限检查（issue #82 / PR B）。
    """
    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = 1
    user.is_superuser = is_superuser
    user.is_active = True
    return user


def _make_mock_db_user():
    """用于 db.get(User, ...) 返回的超管用户，绕过权限检查。"""
    u = MagicMock()
    u.id = 1
    u.is_superuser = True
    u.role = "admin"
    return u


def _make_mock_document(doc_id="doc-1", filename="report.pdf", status_name="indexed"):
    doc = MagicMock()
    doc.id = doc_id
    doc.filename = filename
    doc.title = "Annual Report"
    doc.file_size = 2048
    doc.file_type = MagicMock()
    doc.file_type.value = "pdf"
    doc.status = MagicMock()
    doc.status.value = status_name
    doc.keywords = ["annual", "report"]
    doc.description = "Annual financial report"
    doc.parse_error = None
    doc.created_at = None
    doc.updated_at = None
    doc.uploaded_by = 1
    doc.organization_id = 1
    return doc


def _make_db_get_side_effect(document, user=None):
    """按模型分派的 `db.get` side_effect。

    同一个 `db.get` 被两处共用：`permission_required` 查 `User`（app/core/security.py:82），
    `get_document_for_user` 查 `Document`（app/core/security.py:60）。所以只有 `Document`
    路由能用笼统的 `return_value=`；同时经过权限校验的路由必须按模型分派，
    否则用户 mock 会被当成文档返回（或反之），权限/存在性判断全部失真。
    """
    user = user if user is not None else _make_mock_db_user()

    async def _side_effect(model, *args, **kwargs):
        if model is User:
            return user
        if model is Document:
            return document
        return None

    return _side_effect


def _override_get_db(mock_db=None):
    if mock_db is None:
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.merge = MagicMock(return_value=_make_mock_user())
        mock_db.get = AsyncMock(return_value=_make_mock_db_user())
        mock_db.delete = AsyncMock()
        mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override, mock_db


class TestKnowledgeList:
    def test_list_knowledge_bases(self, client: TestClient):
        """GET /knowledge/ 应返回文档列表。"""
        from app.main import app
        # 本用例只关心列表主流程，权限门禁用超管主体一次性通过
        mock_user = _make_mock_user(is_superuser=True)
        doc = _make_mock_document()

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [doc]

        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert len(data["data"]["data"]) == 1
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_pagination(self, client: TestClient):
        """分页参数应正确返回。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)
        doc1 = _make_mock_document(doc_id="doc-1")
        doc2 = _make_mock_document(doc_id="doc-2", filename="budget.xlsx")

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [doc1, doc2]

        mock_result = MagicMock()
        mock_result.scalar.return_value = 2
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/?page=1&page_size=10",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["data"]["total"] == 2
            assert data["data"]["page"] == 1
            assert data["data"]["page_size"] == 10
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_empty(self, client: TestClient):
        """无文档时应返回空列表。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            assert r.json()["data"]["total"] == 0
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/")
        assert r.status_code in (401, 403)


class TestStatusVocabularyConsistent:
    """第二轮 7.2⑤ 词表收敛契约：同一文档在列表接口与详情接口的 status 必须一致。

    先红实证（本波分支前）：列表 status_map 把 INDEXED 改写为 'completed'
    （knowledge.py status_map），详情端点原样输出 'indexed'（documents.py）——
    同一文档两种词表。唯一词表定为 DB 枚举原词 'indexed'
    （models/document.py DocumentStatus.INDEXED = "indexed"）。
    """

    def test_status_vocabulary_consistent(self, client: TestClient):
        from app.main import app

        mock_user = _make_mock_user(is_superuser=True)
        doc = _make_mock_document(doc_id="doc-vocab-1")
        # 必须用真枚举成员：MagicMock 状态会让 status_map.get 永远 miss，
        # 测不到「列表把 INDEXED 改写为 completed」的真实路径
        doc.status = DocumentStatus.INDEXED
        doc.parsed_at = None
        doc.indexed_at = None
        doc.chunk_count = 0

        override_func, mock_db = _override_get_db()

        # 列表端点经 execute（count + scalars），详情端点经 db.get(Document, id)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [doc]
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.get = AsyncMock(return_value=doc)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r_list = client.get(
                "/api/v1/knowledge/",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r_list.status_code == 200
            item = r_list.json()["data"]["data"][0]

            r_detail = client.get(
                "/api/v1/documents/doc-vocab-1",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r_detail.status_code == 200
            detail = r_detail.json()["data"]

            # 主断言（先红点）：列表 == 详情
            assert item["status"] == detail["status"], (
                f"词表漂移：列表={item['status']!r} vs 详情={detail['status']!r}"
            )
            # 唯一词表 = DB 枚举原词
            assert item["status"] == "indexed"
            assert detail["status"] == "indexed"
            # raw_status 冗余携带原词（列表本波补上；详情已有）
            assert item["raw_status"] == "indexed"
            assert detail["raw_status"] == "indexed"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestKnowledgeStats:
    def test_get_stats_success(self, client: TestClient):
        """GET /knowledge/stats/{org_id} 应返回统计信息。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        # permission_required 不看注入的 current_user，而是用 `db.get(User, ...)` 重查
        # （app/core/security.py:82）。只 override get_current_user 会让校验打到真库，
        # 查不到用户 -> get_user_permissions(db, None, ...) 直接 AttributeError。
        override_func, _mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.get_knowledge_stats = AsyncMock(return_value={
                "total_documents": 42,
                "total_chunks": 1500,
                "document_types": {"pdf": 20, "docx": 22},
            })
            try:
                r = client.get(
                    "/api/v1/knowledge/stats/1",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert data["success"] is True
                assert "stats" in data
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_stats_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/stats/1")
        assert r.status_code in (401, 403)


class TestKnowledgeSearch:
    def test_search_success(self, client: TestClient):
        """POST /knowledge/search 应返回搜索结果。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge = AsyncMock(return_value=[
                {
                    "id": "chunk-1",
                    "text": "Annual report content about revenue",
                    "score": 0.95,
                    "source": {"filename": "report.pdf"},
                    "metadata": {"document_id": "doc-1"},
                    "has_keyword": True,
                    "has_vector": True,
                    "rewrite_hits": 2,
                    "fresh_factor": 1.0,
                }
            ])
            try:
                r = client.post(
                    "/api/v1/knowledge/search",
                    json={"query": "annual report", "top_k": 5, "organization_id": 1},
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert "results" in data
                assert len(data["results"]) > 0
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_search_no_results(self, client: TestClient):
        """无匹配结果应返回空列表。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge = AsyncMock(return_value=[])
            try:
                r = client.post(
                    "/api/v1/knowledge/search",
                    json={"query": "nonexistent term", "organization_id": 1},
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert len(data["results"]) == 0
                assert data["total_count"] == 0
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_search_missing_query(self, client: TestClient):
        """缺少查询参数应返回 422。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/knowledge/search",
                json={},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestKnowledgeSearchModeContract:
    """issue #225：search_mode 真正生效（送达 service）+ 回显一致 + 非法值 422。"""

    @staticmethod
    def _post_search(client: TestClient, payload: dict):
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)
        override_func, _mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
                mock_ks.search_knowledge = AsyncMock(return_value=[])
                r = client.post(
                    "/api/v1/knowledge/search",
                    json=payload,
                    headers={"Authorization": "Bearer test_token"},
                )
            return r, mock_ks
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_forced_mode_reaches_service_and_echoes(self, client: TestClient):
        """显式 search_mode 必须传到 service（修前被链路丢弃），且回显同一值。"""
        r, mock_ks = self._post_search(
            client, {"query": "annual report", "organization_id": 1, "search_mode": "keyword"}
        )
        assert r.status_code == 200
        assert mock_ks.search_knowledge.await_args.kwargs["search_mode"] == "keyword"
        assert r.json()["search_mode"] == "keyword"

    def test_omitted_mode_runs_auto_and_echoes_effective_mode(self, client: TestClient):
        """缺省 → 自动路由（service 收到 None）；回显 = 实际生效档位，不再恒为 'hybrid'。"""
        r, mock_ks = self._post_search(client, {"query": "报销流程", "organization_id": 1})
        assert r.status_code == 200
        assert mock_ks.search_knowledge.await_args.kwargs["search_mode"] is None
        # simple → keyword_only：回显公开模式名 keyword（修前恒回显 hybrid，与生效语义不符）
        assert r.json()["search_mode"] == "keyword"

        r2, mock_ks2 = self._post_search(client, {"query": "如何提交报销申请？", "organization_id": 1})
        assert mock_ks2.search_knowledge.await_args.kwargs["search_mode"] is None
        assert r2.json()["search_mode"] == "hybrid"  # medium → hybrid

    def test_invalid_mode_value_is_422(self, client: TestClient):
        """非法值（含前端旧字段值 'semantic'、大写 'VECTOR'）→ 422，不静默降级。"""
        for bad in ("semantic", "fuzzy", "VECTOR"):
            r, _ = self._post_search(client, {"query": "x", "organization_id": 1, "search_mode": bad})
            assert r.status_code == 422, f"search_mode={bad!r} 必须 422"


class TestKnowledgeSuggestions:
    def test_get_suggestions(self, client: TestClient):
        """GET /knowledge/suggestions 应返回建议列表。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        # 必须连 get_db 一起 mock：`permission_required` 依赖会 `await db.get(User, ...)`
        # 再把它交给 `permission_service`。原先只 override 了 get_current_user，
        # 于是 `db.get` 落回真库、返回 None，请求在依赖阶段就炸了，根本到不了 handler
        # —— 这个用例因此一直红着，且与它想验证的东西无关（issue #83 的 mock 装配类问题）。
        override_db, _mock_db = _override_get_db()
        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.get_search_suggestions = AsyncMock(return_value=[
                "annual report", "annual budget", "annual review"
            ])
            try:
                r = client.get(
                    "/api/v1/knowledge/suggestions?q=annual&organization_id=1",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert len(data["suggestions"]) == 3
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_suggestions_response_matches_model(self, client: TestClient):
        """成功响应必须能通过本端点自己声明的响应模型（issue #91）。

        `SearchSuggestionResponse.query` 是必填字段，而 handler 原先不返回它，
        于是响应模型校验必然失败 —— 端点不可能返回 200。这里直接拿声明的模型
        校验响应体，缺字段会以 ValidationError 的形式当场暴露。
        """
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        override_db, _mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.get_search_suggestions = AsyncMock(return_value=["annual report"])
            try:
                r = client.get(
                    "/api/v1/knowledge/suggestions?q=annual&organization_id=1",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()

                # 通过响应模型 = 端点的契约成立
                parsed = SearchSuggestionResponse.model_validate(data)
                assert parsed.query == "annual"  # 回显原始查询串
                assert parsed.suggestions == ["annual report"]
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_suggestions_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/suggestions?q=test&organization_id=1")
        assert r.status_code in (401, 403)


class TestKnowledgeRebuild:
    def test_rebuild_success(self, client: TestClient):
        """POST /knowledge/rebuild/{id} 应提交重建任务。"""
        from app.main import app
        mock_user = _make_mock_user(is_superuser=True)

        doc = MagicMock()
        doc.id = "doc-1"
        doc.filename = "report.pdf"
        doc.title = "Report"
        doc.file_path = "1/md5.pdf"
        doc.file_type = MagicMock()
        doc.file_type.value = "pdf"
        doc.organization_id = 1

        override_func, mock_db = _override_get_db()
        # 端点走的是 get_document_for_user -> db.get(Document, ...)，不是 db.execute；
        # 且 db.get 还要先给 permission_required 返回 User，故按模型分派。
        mock_db.get = AsyncMock(side_effect=_make_db_get_side_effect(doc))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.delete_es_by_document_id = AsyncMock(return_value=None)

            with patch("app.api.v1.endpoints.knowledge.kafka_producer") as mock_kafka:
                mock_kafka.send_message = AsyncMock(return_value=None)

                try:
                    r = client.post(
                        "/api/v1/knowledge/rebuild/doc-1",
                        headers={"Authorization": "Bearer test_token"}
                    )
                    assert r.status_code == 200
                    data = r.json()
                    assert data["success"] is True
                finally:
                    app.dependency_overrides.pop(get_db, None)
                    app.dependency_overrides.pop(get_current_user, None)

    def test_rebuild_no_auth(self, client: TestClient):
        """未认证重建请求应返回 401/403。"""
        r = client.post("/api/v1/knowledge/rebuild/doc-1")
        assert r.status_code in (401, 403)


class TestKnowledgeGraphNodeAttribution:
    """GET /knowledge/graph —— 真实实体的 type/description 不得被关系目标占位吞掉。

    背景（a8a3d155 写链打通后数据一有就现形）：旧实现的单遍循环里，先被处理的
    实体先把它的关系目标以 UNKNOWN 占位插入 nodes；等循环走到那个目标自己的
    实体条目时，又因按名去重被 continue 跳过——真实类型/描述永久丢失（实测
    56 实体中 13 个被吞）。修复：两遍处理，真实实体先占位，目标解析只补空位。
    变异点：还原单遍循环 → 本用例红（数据层 type 变成 UNKNOWN）。
    """

    def test_entity_type_survives_relation_target_prebinding(self, client: TestClient):
        from app.main import app
        from app.services.auth_service import auth_service
        from app.services.graph_rag_service import graph_rag_service

        org = 5
        graph = graph_rag_service._graph_for(org)
        graph.clear()
        hub_key = graph_rag_service._normalize_entity("Hub")
        layer_key = graph_rag_service._normalize_entity("数据层")
        graph[hub_key].update({
            "entity_name": "Hub",
            "entity_type": "PRODUCT",
            "description": "中枢实体",
            "occurrences": 1,
        })
        graph[hub_key]["relationships"].append({"target": layer_key, "relation": "PART_OF"})
        graph[layer_key].update({
            "entity_name": "数据层",
            "entity_type": "CONCEPT",
            "description": "存储层描述",
            "occurrences": 1,
        })

        mock_user = _make_mock_user(is_superuser=True)
        mock_user.organization_id = org

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[auth_service.get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/graph",
                headers={"Authorization": "Bearer test_token"},
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(auth_service.get_current_user, None)
            graph.clear()

        assert r.status_code == 200, r.text
        body = r.json()
        nodes = {n["id"]: n for n in body["nodes"]}
        assert nodes["数据层"]["type"] == "CONCEPT", (
            f"真实实体类型被关系目标占位吞掉: {nodes}"
        )
        assert nodes["数据层"]["description"] == "存储层描述"
        assert nodes["Hub"]["type"] == "PRODUCT"
        assert any(
            e["source"] == "Hub" and e["target"] == "数据层" and e["relation"] == "PART_OF"
            for e in body["edges"]
        ), "边不得因修复丢失"
