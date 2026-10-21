"""issue #301 批 C —— 统计端点零值族诚实化（假成功家族第 14-21/26/27 条，10 处）。

修前病征（复现原文见 exec_stats301.md / PR 正文，全部 10 处先复现后修）：
任一查询/调用异常在 except 里回落「零值/空列表 200」，与真实数据不可区分 ——
- /monitoring/org-summary → {"organizations": []}（像「零组织」）
- /monitoring/admin-summary → 5 项 KPI 全零（像「系统被清空」）
- /monitoring/logs → 读文件失败 → {"logs": [], "total": 0}（与「确实无日志」同形）
- /monitoring/rag-eval(-batch) → 评委挂 → 0/0/0 分 200（批量时故障条目 0 分计入均值）
- /knowledge/rag-stats → 命中率全零 + 兜底趋势 7 桶 vs 成功 8 桶（形状漂移）
- /knowledge/graph → 假空图谱（页面空白像「没有实体」）
- /users/stats → 个人面板全零
- /organizations/tree → 空树 200（像「组织全被删」）
- /organizations/{id}/stats → 0/0

修法（逐处裁定，降级形态 vs 5xx 的选择理由见各用例 docstring）：
① 带身份的降级形态（200 + degraded:true + reason，既有键一个不改）：
   org-summary / admin-summary / rag-stats / users/stats —— 面板类聚合统计，
   消费方（dashboard/admin.vue、dashboard/user.vue、profile 页）只读既有键，
   加字段不破坏契约；且 admin.vue / user.vue 用 Promise.all 并发加载，5xx 会
   连带炸掉同页其它面板，「带身份的空数据」是更合适的页面级降级。
② 显式 5xx：
   logs → 500（服务端本地读失败，error_code=LOG_READ_FAILED；无前端消费方，
   状态码是最直接的运维信号）；
   rag-eval / rag-eval-batch → 502 RAG_EVAL_UNAVAILABLE（评委是外部 LLM 服务，
   对齐 #292 检索后端不可用的 502 先例；0 分是合法分数值，必须与「测量失败」可分）；
   graph / organizations tree / organizations stats → 503 SERVICE_UNAVAILABLE
   （「查不了」与「空/0」必须可分；组织管理页已有「加载失败+重试」错误态承接）。

变异判别（逐处精确红，矩阵见 exec_stats301.md）：
- 还原任一 except 的零值/空值回落 → 对应用例红；
- evaluator._score 还原吞异常（return 0.0） → rag-eval 两用例红（502 变 0 分 200）；
- evaluate_batch 去掉条目级标注 → partial 用例红；
- rag-stats 趋势还原 7 桶 → 形状对拍用例红。
"""

import asyncio
import builtins
import io
import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base, Organization, User
from app.models.chat import ChatMessage, ChatSession, MessageType
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import user_organization
from app.rag.errors import EvalJudgeUnavailableError
from app.services.auth_service import auth_service

# ── 公共夹具 ──────────────────────────────────────────────────────


def _boom_db() -> MagicMock:
    """一切查询即炸的 fake 会话（故障注入的最小形态）。"""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("301c: db down"))
    db.get = AsyncMock(side_effect=RuntimeError("301c: db down"))
    db.close = AsyncMock()
    return db


def _superuser(org=5) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        organization_id=org,
        is_superuser=True,
        role="admin",
        is_active=True,
        username="stats301",
    )


def _override_http(user, db):
    """统一挂 get_current_user / auth_service.get_current_user / get_db 覆盖。"""
    from app.main import app

    async def _u():
        return user

    async def _db():
        return db

    app.dependency_overrides[get_current_user] = _u
    app.dependency_overrides[auth_service.get_current_user] = _u
    app.dependency_overrides[get_db] = _db

    def _cleanup():
        for k in (get_current_user, auth_service.get_current_user, get_db):
            app.dependency_overrides.pop(k, None)

    return _cleanup


class _FailCompletions:
    """评委 LLM「不可达」：create 立即抛（等价断网 / key 失效）。"""

    async def create(self, *a, **k):
        raise ConnectionError("301c: judge LLM unreachable")


class _FailChat:
    def __init__(self):
        self.completions = _FailCompletions()


class _FailOpenAI:
    def __init__(self, **k):
        self.chat = _FailChat()


# ════════════════════════════════════════════════════════════════════
# ① 面板类统计（org-summary / admin-summary / rag-stats / users stats）
#    降级形态：200 + degraded=true + reason，既有键不变
# ════════════════════════════════════════════════════════════════════


class TestDegradedPanels:
    def test_org_summary_query_failure_is_degraded_not_fake_empty(self, client: TestClient):
        """#14：查询异常 → 带身份的降级（degraded+reason），不再是裸空列表。

        选①不选 5xx：admin.vue 的「组织概览」表格与同页其它面板共用一个
        async 流程，且 organizations 键是既有契约 —— 空表 + 身份标记即可，
        不炸整页。变异验证：还原 `return {"organizations": []}` → 本用例红。
        """
        _cleanup = _override_http(_superuser(), _boom_db())
        try:
            r = client.get("/api/v1/monitoring/org-summary")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["organizations"] == []
        assert j["degraded"] is True, f"查询失败必须带身份（修前是裸空列表）: {j}"
        assert j["reason"], "必须说明降级原因"

    def test_admin_summary_query_failure_is_degraded_not_fake_zero(self, client: TestClient):
        """#15：查询异常 → 5 项 KPI 全零 + degraded 身份，不再是「系统被清空」的裸零。

        选①不选 5xx：本端点是 admin.vue Promise.all 的首个成员，5xx 会连带
        炸掉审计日志与 LLM 统计两块面板；降级形态保留页面其余部分可用。
        变异验证：还原裸零 return（无 degraded）→ 本用例红。
        """
        _cleanup = _override_http(_superuser(), _boom_db())
        try:
            r = client.get("/api/v1/monitoring/admin-summary")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["total_users"] == 0 and j["total_documents"] == 0
        assert j["total_sessions"] == 0 and j["active_users_24h"] == 0
        assert j["total_organizations"] == 0
        assert j["degraded"] is True, f"KPI 全零必须带身份（修前与真实零不可分）: {j}"
        assert j["reason"]

    def test_user_stats_query_failure_is_degraded_not_fake_zero(self, client: TestClient):
        """#21：个人统计查询异常 → 面板全零 + degraded 身份。

        选①不选 5xx：users/stats 在 user.vue 的 Promise.all 里与最近会话、
        RAG 统计并发，5xx 会连带炸掉整页；profile 页则静默吞错误。
        变异验证：还原裸零 return（无 degraded）→ 本用例红。
        """
        _cleanup = _override_http(_superuser(org=None), _boom_db())
        try:
            r = client.get("/api/v1/users/stats")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["conversation_count"] == 0 and j["message_count"] == 0
        assert j["activity_trend"] == [0] * 7
        assert j["degraded"] is True, f"面板全零必须带身份: {j}"
        assert j["reason"]

    def test_rag_stats_failure_is_degraded_and_trend_shape_matches_success(
        self, client: TestClient
    ):
        """#19：rag-stats 异常 → 降级身份 + 趋势桶数与成功路径同形（8 桶，修前 7）。

        选①：admin.vue 与 user.vue 都消费本端点（user.vue 在 Promise.all 里）。
        变异验证：还原 `[0]*7` 或去掉 degraded → 本用例红。
        """
        import time as _time

        now = _time.time()
        ok_stub = SimpleNamespace(
            get_metrics=lambda **k: {
                "retrieval_total": 4,
                "retrieval_hit": 2,
                "grounded_total": 2,
                "grounded_hit": 1,
                "avg_latency_ms": 12.5,
                "avg_docs_per_query": 3.0,
            },
            _pipeline=SimpleNamespace(
                metrics=SimpleNamespace(
                    _events={
                        "retrieval": [
                            (now - 60, 1),
                            (now - 120, 1),
                            (now - 180, 1),
                            (now - 240, 1),
                        ],
                        "retrieval_hit": [(now - 60, 1), (now - 120, 1)],
                    }
                )
            ),
        )

        class _FailMetrics:
            def get_metrics(self, **k):
                raise RuntimeError("301c: metrics down")

        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch("app.services.rag_service.rag_service", new=_FailMetrics()):
                r_fail = client.get("/api/v1/knowledge/rag-stats")
            with patch("app.services.rag_service.rag_service", new=ok_stub):
                r_ok = client.get("/api/v1/knowledge/rag-stats")
        finally:
            _cleanup()

        assert r_fail.status_code == 200, r_fail.text
        jf = r_fail.json()
        assert jf["hit_rate"] == 0 and jf["total_queries_7d"] == 0
        assert jf["degraded"] is True, f"异常必须带身份: {jf}"
        assert jf["reason"]

        assert r_ok.status_code == 200, r_ok.text
        jo = r_ok.json()
        # 正常路径数值如实（2/4 命中 = 50.0）且不带降级身份
        assert jo["hit_rate"] == 50.0
        assert jo["total_queries_7d"] == 4
        assert jo.get("degraded") is not True

        # 形状对拍：降级与成功的趋势桶数必须一致（修前 7 vs 8 漂移）
        assert len(jf["hit_rate_trend_7d"]) == len(jo["hit_rate_trend_7d"]) == 8, (
            f"趋势桶数漂移：fail={len(jf['hit_rate_trend_7d'])} ok={len(jo['hit_rate_trend_7d'])}"
        )


# ════════════════════════════════════════════════════════════════════
# ② 显式 5xx 族（logs / rag-eval / rag-eval-batch / graph / org tree / org stats）
# ════════════════════════════════════════════════════════════════════


def _log_file_path() -> str:
    from app.api.v1.endpoints import monitoring as mon
    from app.core.config import settings

    src = mon.__file__
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(src))))
    return (
        settings.LOG_FILE
        if os.path.isabs(settings.LOG_FILE)
        else os.path.join(root, settings.LOG_FILE)
    )


class TestLogsHonesty:
    def _patch_log_file(self, content: str | None, oserror: bool = False):
        """把日志文件的 exists/open 钉成确定态。oserror=True → 读取抛 PermissionError。"""
        target = _log_file_path()
        real_exists = os.path.exists
        real_open = builtins.open

        def fake_exists(p):
            return True if str(p) == str(target) else real_exists(p)

        def fake_open(file, *a, **k):
            if str(file) == str(target):
                if oserror:
                    raise PermissionError(13, "Permission denied")
                return io.StringIO(content or "")
            return real_open(file, *a, **k)

        return patch.object(os.path, "exists", fake_exists), patch.object(
            builtins, "open", fake_open
        )

    def test_logs_read_failure_is_500_not_fake_zero(self, client: TestClient):
        """#16：读文件失败 → 显式 500（LOG_READ_FAILED），不再是假「零日志」。

        选②不选①：本端点无前端消费方（纯运维 API），「0 条日志」正是排障
        人员据以判断的假象；状态码是最直接的分辨信号。
        变异验证：还原 `return {"logs": [], ...}` → 本用例红（变 200 空列表）。
        """
        p1, p2 = self._patch_log_file(None, oserror=True)
        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with p1, p2:
                r = client.get("/api/v1/monitoring/logs")
        finally:
            _cleanup()

        assert r.status_code == 500, (
            f"读失败必须显式报错（修前 200 空列表）: {r.status_code} {r.text[:200]}"
        )
        j = r.json()
        assert j.get("error_code") == "LOG_READ_FAILED", j

    def test_logs_readable_file_still_streams_truthfully(self, client: TestClient):
        """正常路径回归：可读文件 → 真实条目被解析返回（行为不变）。"""
        lines = (
            '{"timestamp": "t1", "level": "INFO", "logger": "x", "message": "hello"}\n'
            '{"timestamp": "t2", "level": "ERROR", "logger": "y", "message": "boom"}\n'
        )
        p1, p2 = self._patch_log_file(lines)
        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with p1, p2:
                r = client.get("/api/v1/monitoring/logs", params={"level": "ALL"})
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["total"] == 2
        assert {e["message"] for e in j["logs"]} == {"hello", "boom"}

    def test_logs_missing_file_still_empty_200(self, client: TestClient):
        """正常回归：文件不存在（还没写过日志）仍是 200 空列表——这是事实，不是故障。"""
        target = _log_file_path()
        real_exists = os.path.exists
        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch.object(
                os.path, "exists", lambda p: False if str(p) == str(target) else real_exists(p)
            ):
                r = client.get("/api/v1/monitoring/logs")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        assert r.json()["logs"] == [] and r.json()["total"] == 0


class TestRagEvalHonesty:
    def test_evaluator_score_raises_when_judge_unavailable(self):
        """evaluator 源头：评委调用失败抛 EvalJudgeUnavailableError，不再返回 0.0。

        变异验证：`_score` 还原 `return 0.0, str(e)` → 本用例红。
        """
        from app.rag.evaluator import RAGEvaluator

        ev = RAGEvaluator(client=_FailOpenAI(), model="m")
        with pytest.raises(EvalJudgeUnavailableError):
            asyncio.run(ev.evaluate(question="q", answer="a", context=[]))

        # 客户端未配置（None）同样属于「测量不可知」，不是 0 分
        with pytest.raises(EvalJudgeUnavailableError):
            asyncio.run(RAGEvaluator(client=None).evaluate(question="q", answer="a", context=[]))

    def test_rag_eval_single_judge_down_returns_502_not_zero_scores(self, client: TestClient):
        """#17：评委挂 → 502 RAG_EVAL_UNAVAILABLE，不再是 0/0/0 分 200。

        选②不选①：0.0 是合法分数（代表质量极差），降级形态无法把它与
        「测量失败」分开；评委是外部 LLM 服务 → 502（对齐 #292 先例）。
        变异验证：① 还原端点 except 吞异常 或 ② 还原 _score 吞异常 → 都红。
        """
        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch("openai.AsyncOpenAI", _FailOpenAI):
                r = client.post(
                    "/api/v1/monitoring/rag-eval",
                    json={"question": "q", "answer": "a", "context": []},
                )
        finally:
            _cleanup()

        assert r.status_code == 502, (
            f"评委不可用不得呈现为分数（修前 200+0/0/0）: {r.status_code} {r.text[:200]}"
        )
        j = r.json()
        assert j.get("error_code") == "RAG_EVAL_UNAVAILABLE", j
        assert "faithfulness" not in j, "故障响应里不得出现冒充分数的字段"

    def test_rag_eval_single_judge_ok_returns_real_scores(self, client: TestClient):
        """正常路径回归：评委可用 → 真实分数原样返回（行为不变）。"""
        scores = {"f": 0.9, "r": 0.8, "c": 0.7}

        class _OkOpenAI:
            def __init__(self, **k):
                calls = {"n": 0}

                class _C:
                    async def create(self, *a, **kw):
                        val = [scores["f"], scores["r"], scores["c"]][calls["n"]]
                        calls["n"] += 1
                        return SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(content=f'{{"score": {val}}}')
                                )
                            ]
                        )

                self.chat = SimpleNamespace(completions=_C())

        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch("openai.AsyncOpenAI", _OkOpenAI):
                r = client.post(
                    "/api/v1/monitoring/rag-eval",
                    json={"question": "q", "answer": "a", "context": [{"text": "ctx"}]},
                )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert (j["faithfulness"], j["relevancy"], j["context_precision"]) == (0.9, 0.8, 0.7)

    def test_rag_eval_batch_all_fail_returns_502_not_all_zero(self, client: TestClient):
        """#18a：整批评委不可用 → 502，不再是 count 0 全零 200（连痕迹都没有）。

        变异验证：还原端点 `except Exception → 全零 return` 或 `_score` 吞异常 → 红。
        """
        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch("openai.AsyncOpenAI", _FailOpenAI):
                r = client.post(
                    "/api/v1/monitoring/rag-eval-batch",
                    json={
                        "items": [
                            {"question": "q1", "answer": "a1", "context": []},
                            {"question": "q2", "answer": "a2", "context": []},
                        ]
                    },
                )
        finally:
            _cleanup()

        assert r.status_code == 502, (
            f"全批失败不得呈现为「全零成功」: {r.status_code} {r.text[:200]}"
        )
        assert r.json().get("error_code") == "RAG_EVAL_UNAVAILABLE"

    def test_rag_eval_batch_partial_failure_marks_and_excludes_from_mean(self, client: TestClient):
        """#18b：部分条目评委失败 → 标注 failures（含下标）并从均值剔除。

        第 1 条 3 次评审调用全成功（0.9/0.8/0.7），第 2 条第 4 次调用失败。
        修前：故障条目以 0 分混入 → avg 0.45；修后：avg == 0.9（仅成功条目），
        failed_count=1、failures=[{index:1,...}]。
        变异验证：evaluate_batch 去掉条目级 try/标注（还原旧循环）→ 本用例红
        （异常上抛成 502，或 0 分混入均值断言不过）。
        """
        seq = iter([0.9, 0.8, 0.7])

        class _PartialOpenAI:
            def __init__(self, **k):
                class _C:
                    async def create(self, *a, **kw):
                        try:
                            val = next(seq)
                        except StopIteration:
                            raise ConnectionError("301c: judge LLM unreachable (item 2)") from None
                        return SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(content=f'{{"score": {val}}}')
                                )
                            ]
                        )

                self.chat = SimpleNamespace(completions=_C())

        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch("openai.AsyncOpenAI", _PartialOpenAI):
                r = client.post(
                    "/api/v1/monitoring/rag-eval-batch",
                    json={
                        "items": [
                            {"question": "q1", "answer": "a1", "context": []},
                            {"question": "q2", "answer": "a2", "context": []},
                        ]
                    },
                )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["count"] == 1, f"count 只计真正评出分的条目: {j}"
        assert j["failed_count"] == 1
        assert j["failures"] == [{"index": 1, "error": "judge_unavailable"}], j
        assert len(j["results"]) == 1
        assert j["results"][0] == {"faithfulness": 0.9, "relevancy": 0.8, "context_precision": 0.7}
        assert j["avg_faithfulness"] == 0.9, (
            f"均值不得把故障条目的 0 分混入（修前 avg=0.45）: {j['avg_faithfulness']}"
        )


class TestGraphHonesty:
    def test_graph_backend_failure_returns_503_not_empty_graph(self, client: TestClient):
        """#20：图谱加载/分析异常 → 503，不再是「空图谱 200」（页面空白像没实体）。

        变异验证：还原 `return {"nodes": [], "edges": [], "analytics": {}}` → 红。
        """
        from app.services.graph_rag_service import GraphRAGService

        async def _boom(self, organization_id=1):
            raise RuntimeError("301c: graph backend down")

        _cleanup = _override_http(_superuser(), MagicMock())
        try:
            with patch.object(GraphRAGService, "get_analytics", _boom):
                r = client.get("/api/v1/knowledge/graph")
        finally:
            _cleanup()

        assert r.status_code == 503, f"图谱故障不得伪装成空图谱: {r.status_code} {r.text[:200]}"
        assert r.json().get("error_code") == "SERVICE_UNAVAILABLE"

    def test_graph_legit_empty_still_200(self, client: TestClient):
        """正常回归：知识库确实没有实体时——空图谱仍是 200 的事实陈述（非故障）。"""
        from app.services.graph_rag_service import graph_rag_service

        graph_rag_service._graph_for(5).clear()  # 进程级单例，防其它用例残留
        _cleanup = _override_http(_superuser(), _boom_db())
        try:
            r = client.get("/api/v1/knowledge/graph")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        j = r.json()
        assert j["nodes"] == [] and j["edges"] == []
        assert j["analytics"]["total_entities"] == 0


class TestOrganizationEndpointsHonesty:
    def test_org_tree_db_failure_returns_503_not_empty_tree(self, client: TestClient):
        """#26：组织树查询异常 → 503，不再是「空树 200」（像组织全被删）。

        组织管理页已有「加载失败+重试」错误态（X-Silent-Error 静默头），
        正是为承接这种显式失败而存在。
        变异验证：service 还原 `return []` → 端点是 200 success:true data:[] → 红。
        """
        _cleanup = _override_http(_superuser(), _boom_db())
        try:
            r = client.get("/api/v1/organizations/tree")
        finally:
            _cleanup()

        assert r.status_code == 503, f"树查询失败不得伪装成空树: {r.status_code} {r.text[:200]}"
        j = r.json()
        assert j.get("error_code") == "SERVICE_UNAVAILABLE"
        assert j.get("data") is None, "故障响应不得携带「空树」数据形态"

    def test_org_tree_service_raises_instead_of_empty_list(self):
        """service 直测：db.execute 抛错 → ServiceUnavailableError，绝不返回 []。"""
        from app.services.organization_service import organization_service

        with pytest.raises(Exception) as ei:
            asyncio.run(organization_service.get_organization_tree(_boom_db(), _superuser()))
        assert getattr(ei.value, "status_code", None) == 503, ei.value

    def test_org_stats_db_failure_returns_503_not_zero_zero(self, client: TestClient):
        """#27：组织统计查询异常 → 503，「0 成员 0 文档」不得冒充真实事实。

        变异验证：service 还原 `return {"member_count": 0, "document_count": 0}` → 红。
        """
        _cleanup = _override_http(_superuser(org=1), _boom_db())
        try:
            r = client.get("/api/v1/organizations/1/stats")
        finally:
            _cleanup()

        assert r.status_code == 503, f"统计失败不得伪装成 0/0: {r.status_code} {r.text[:200]}"
        assert r.json().get("error_code") == "SERVICE_UNAVAILABLE"

    def test_org_stats_service_raises_instead_of_zeros(self):
        """service 直测：查询抛错 → ServiceUnavailableError，绝不返回 0/0。"""
        from app.services.organization_service import organization_service

        with pytest.raises(Exception) as ei:
            asyncio.run(organization_service.get_org_stats(_boom_db(), 1))
        assert getattr(ei.value, "status_code", None) == 503, ei.value


# ════════════════════════════════════════════════════════════════════
# 正常路径回归对照（真实 SQLite 建表造数：数值必须如实）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_normal_paths_reflect_real_counts(client: TestClient):
    """有数据时数值如实（org-summary / admin-summary / users stats / tree / org stats）。

    真建表真插入：org 55（2 用户/2 文档/2 会话；其中 1 篇 INDEXED）+ org 66
    （3 用户/1 文档，父级 55）。任何降级身份（degraded）都不得出现在正常响应里。
    """
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
                Organization(id=55, name="Org55", owner_id=0),
                Organization(id=66, name="Org66", owner_id=0, parent_id=55, level=2),
            ]
        )
        u55 = [
            User(
                username=f"u55-{i}", email=f"u55-{i}@e.com", hashed_password="x", organization_id=55
            )
            for i in range(2)
        ]
        u66 = [
            User(
                username=f"u66-{i}", email=f"u66-{i}@e.com", hashed_password="x", organization_id=66
            )
            for i in range(3)
        ]
        s.add_all(u55 + u66)
        await s.flush()
        # member_count 口径来自 user_organization 关联表（非 User.organization_id）
        await s.execute(
            user_organization.insert(),
            [
                {"user_id": u55[0].id, "organization_id": 55},
                {"user_id": u55[1].id, "organization_id": 55},
            ],
        )
        # 趋势窗口外的时间戳：users/stats 的 trend 查询用 cast(created_at, Date)，
        # sqlite 的 CAST AS DATE 返回整数（NUMERIC 亲和性）会让处理器炸——那是
        # 测试库方言产物（MySQL 无此问题）。旧时间戳让趋势查询匹配零行，
        # 计数类查询不受影响，正常路径对照仍是真表真数。
        old = datetime.now() - timedelta(days=30)
        s.add_all(
            [
                Document(
                    id="d55-0",
                    filename="a.pdf",
                    file_path="/tmp/a.pdf",
                    file_size=10,
                    file_type=DocumentType.PDF,
                    organization_id=55,
                    uploaded_by=u55[0].id,
                    status=DocumentStatus.INDEXED,
                    created_at=old,
                ),
                Document(
                    id="d55-1",
                    filename="b.pdf",
                    file_path="/tmp/b.pdf",
                    file_size=20,
                    file_type=DocumentType.PDF,
                    organization_id=55,
                    uploaded_by=u55[0].id,
                    created_at=old,
                ),
                Document(
                    id="d66-0",
                    filename="c.pdf",
                    file_path="/tmp/c.pdf",
                    file_size=30,
                    file_type=DocumentType.PDF,
                    organization_id=66,
                    uploaded_by=u66[0].id,
                    created_at=old,
                ),
            ]
        )
        sess55 = [
            ChatSession(
                id=str(uuid.uuid4()),
                user_id=u55[0].id,
                organization_id=55,
                title=f"s{i}",
                created_at=old,
            )
            for i in range(2)
        ]
        s.add_all(sess55)
        await s.flush()
        s.add_all(
            [
                ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=sess55[0].id,
                    content=f"m{i}",
                    message_type=MessageType.USER,
                )
                for i in range(3)
            ]
        )
        await s.commit()
        user55_id = u55[0].id

    user = SimpleNamespace(
        id=user55_id,
        organization_id=55,
        is_superuser=True,
        role="admin",
        is_active=True,
        username="stats301b",
    )

    async def _ref():
        return user

    async def _db_gen():
        async with factory() as session:
            yield session

    from app.main import app

    app.dependency_overrides[get_db] = _db_gen
    app.dependency_overrides[get_current_user] = _ref
    app.dependency_overrides[auth_service.get_current_user] = _ref
    try:
        r_org = client.get("/api/v1/monitoring/org-summary")
        r_admin = client.get("/api/v1/monitoring/admin-summary")
        r_me = client.get("/api/v1/users/stats")
        r_tree = client.get("/api/v1/organizations/tree")
        r_stats = client.get("/api/v1/organizations/55/stats")
    finally:
        for k in (get_db, get_current_user, auth_service.get_current_user):
            app.dependency_overrides.pop(k, None)
        await engine.dispose()

    assert r_org.status_code == 200, r_org.text
    orgs = {o["org_name"]: o for o in r_org.json()["organizations"]}
    assert orgs["Org55"]["user_count"] == 2 and orgs["Org55"]["doc_count"] == 2
    assert orgs["Org66"]["user_count"] == 3 and orgs["Org66"]["doc_count"] == 1
    assert "degraded" not in r_org.json(), "正常响应不得带降级身份"

    assert r_admin.status_code == 200, r_admin.text
    ja = r_admin.json()
    assert ja["total_users"] == 2 and ja["total_documents"] == 2
    assert ja["total_sessions"] == 2 and ja["total_organizations"] == 2  # 超管全系统口径
    assert "degraded" not in ja

    assert r_me.status_code == 200, r_me.text
    jm = r_me.json()
    assert jm["conversation_count"] == 2 and jm["message_count"] == 3
    assert jm["file_count"] == 2 and jm["knowledge_count"] == 1  # 仅 INDEXED 计入
    assert jm["storage_used"] == 30
    assert jm["activity_trend"] == [0] * 7  # 全部活动在 7 天窗口外：如实为空
    assert jm["degraded"] is False and jm["reason"] is None, (
        f"正常响应必须显式非降级（schema 新键的默认态）: {jm.get('degraded')}"
    )

    assert r_tree.status_code == 200, r_tree.text
    tree = r_tree.json()["data"]
    assert [n["name"] for n in tree] == ["Org55"]
    assert [c["name"] for c in tree[0]["children"]] == ["Org66"]

    assert r_stats.status_code == 200, r_stats.text
    assert r_stats.json()["data"] == {"member_count": 2, "document_count": 2}
