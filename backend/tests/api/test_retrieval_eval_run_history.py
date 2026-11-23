"""评测运行历史：持久化 opt-in、组织隔离、导出一致性、失败计数与趋势。

沿用 test_retrieval_eval_api 的真实 sqlite + dependency_overrides 基座；本文件的
每个判别用例都在 docstring 里写明变异点（删掉隔离条件/改默认持久化/把失败算进
分母等会让哪个断言变红）。
"""

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.retrieval_eval import RetrievalEvalCase, RetrievalEvalRun

# 复用 P2 的两组织基座：取 fixture 包裹前的 async generator，重新声明为本模块 fixture。
# 直接 import `env` 名字会拿到 FixtureFunction 包装对象（不是 yield 出的状态对象）。
from tests.api.test_retrieval_eval_api import DOC_A1  # noqa: E402
from tests.api.test_retrieval_eval_api import env as _base_env_fixture  # noqa: E402

env = pytest.fixture(_base_env_fixture.__wrapped__)

CASES = "/api/v1/knowledge/eval/cases"
RUN = "/api/v1/knowledge/eval/run"
RUNS = "/api/v1/knowledge/eval/runs"
ORG_A, ORG_B = 1, 2
USER_A, USER_B = 11, 22


def _as(env, uid=USER_A, org=ORG_A):
    from tests.api.test_retrieval_eval_api import _make_user

    env.state.user = _make_user(uid, org)


async def _case(env, query="q", ids=None):
    from tests.api.test_retrieval_eval_api import _create

    return await _create(env, query=query, ids=ids if ids is not None else [DOC_A1])


async def _fake_run(env, mapping=None, errors=()):
    from tests.api.test_retrieval_eval_api import _FakeKnowledge

    return _FakeKnowledge(mapping or {}, error_queries=errors)


async def _seed_runs(env, count=3):
    """直接写入运行行：用 service 的哈希函数保证与真实运行同一口径。"""
    from app.services import retrieval_eval_service as svc

    case = await _case(env)
    runs = []
    base_time = datetime(2026, 11, 20, 12, 0, 0)
    async with env.factory() as s:
        db_case = await s.get(RetrievalEvalCase, case["id"])
        fingerprint = svc.case_set_fingerprint([db_case])
        for index in range(count):
            metrics = {
                "recall_at_k": 0.5 + index * 0.1,
                "mrr": 0.4 + index * 0.1,
                "document_hit_at_k": 0.6,
                "hit_at_k": 0.6,
            }
            counts = {"cases": 1, "evaluated": 1, "errors": 0}
            if index == count - 1:
                # 最后一次运行失败：指标全 null，不能污染趋势均值。
                metrics = {
                    "recall_at_k": None,
                    "mrr": None,
                    "document_hit_at_k": None,
                    "hit_at_k": None,
                }
                counts = {"cases": 1, "evaluated": 0, "errors": 1}
            run = RetrievalEvalRun(
                id=str(uuid4()),
                organization_id=ORG_A,
                created_by=USER_A,
                top_k=5,
                case_ids=[case["id"]],
                case_scope="subset",
                case_set_hash=fingerprint,
                counts=counts,
                metrics=metrics,
                error_case_ids=[case["id"]] if counts["errors"] else [],
                results=[
                    {
                        "case_id": case["id"],
                        "query": "secret-query-should-stay-in-org",
                        "status": "error" if counts["errors"] else "ok",
                    }
                ],
                # 显式递增时间：同秒时 DB 按 id 倒序，会让趋势顺序不可预测。
                created_at=base_time + timedelta(hours=index),
            )
            s.add(run)
            runs.append(run)
        await s.commit()
    return runs, fingerprint


# ── 用例集哈希（内容寻址 / 变异判别）───────────────────────────────────


def test_case_set_fingerprint_normalizes_order_and_tracks_content():
    """哈希按用例集内容计算：输入顺序无关；query/金标/集合成员变化必变。

    变异点：hash 返回常量 → 全部相等断言红；hash 用原始 list 顺序拼接 →
    reversed 输入断言红；hash 忽略 query / expected_document_ids / 集合成员
    → 对应不等断言红。直接调用纯函数，不读源码字符串。
    """
    from app.services.retrieval_eval_service import case_set_fingerprint

    case_a = SimpleNamespace(
        id="case-a", query="退款多久到账", expected_document_ids=["doc-1", "doc-2"]
    )
    case_b = SimpleNamespace(id="case-b", query="合同何时生效", expected_document_ids=["doc-3"])
    baseline = case_set_fingerprint([case_a, case_b])

    # 顺序归一化：集合相同则 hash 相同（比较前不必关心用例创建/查询顺序）。
    assert case_set_fingerprint([case_b, case_a]) == baseline
    assert len(baseline) == 64

    changed_query = SimpleNamespace(
        id="case-a", query="退款多久能到", expected_document_ids=["doc-1", "doc-2"]
    )
    assert case_set_fingerprint([changed_query, case_b]) != baseline

    changed_golden = SimpleNamespace(
        id="case-a", query="退款多久到账", expected_document_ids=["doc-1", "doc-9"]
    )
    assert case_set_fingerprint([changed_golden, case_b]) != baseline

    # 成员变化（全量 vs 子集语义）也必须反映在集合哈希里。
    assert case_set_fingerprint([case_a]) != baseline


async def test_case_set_hash_ignores_top_k_but_persists_it(env):
    """top_k 是评测口径，故意不进用例集 hash；但必须原样落库供比较时校验。

    变异点：把 top_k 混入 hash（或让 hash 随 top_k 变化）→ 两次持久化后
    case_set_hash 相等断言变红；persist_run 漏存 top_k → top_k 集合断言红。
    """
    from unittest.mock import patch

    _as(env)
    case = await _case(env, query="topk-stable")
    fake = await _fake_run(env, {"topk-stable": [DOC_A1]})
    run_ids: list[str] = []
    for top_k in (3, 5):
        with patch(
            "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake
        ):
            response = await env.client.post(
                RUN,
                json={"case_ids": [case["id"]], "top_k": top_k, "persist": True},
            )
        assert response.status_code == 200, response.text
        run_ids.append(response.json()["data"]["run_id"])

    async with env.factory() as session:
        rows = [await session.get(RetrievalEvalRun, run_id) for run_id in run_ids]
    assert rows[0].case_set_hash == rows[1].case_set_hash
    assert {row.top_k for row in rows} == {3, 5}
    assert rows[0].case_ids == rows[1].case_ids == [case["id"]]


# ── persist opt-in（默认不写库）─────────────────────────────────────────


async def test_default_run_is_non_persistent_opt_in_writes(env):
    """默认 /eval/run 不落库；persist=true 与 POST /eval/runs 才写。

    变异点：把 service 默认 persist 改成 True 或去掉 if persist 分支 → 第一个
    断言（rows == [] / persisted is False / run_id 缺失）立即红。
    """
    from unittest.mock import patch

    _as(env)
    case = await _case(env, query="secret-default")
    fake = await _fake_run(env, {"secret-default": [DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        plain = await env.client.post(RUN, json={"case_ids": [case["id"]]})
    assert plain.status_code == 200, plain.text
    payload = plain.json()["data"]
    assert payload["persisted"] is False
    assert "run_id" not in payload
    async with env.factory() as s:
        assert (await s.execute(select(RetrievalEvalRun))).scalars().all() == []

    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        opted = await env.client.post(
            RUN, json={"case_ids": [case["id"]], "persist": True, "top_k": 5}
        )
    assert opted.status_code == 200, opted.text
    run_id = opted.json()["data"]["run_id"]
    assert opted.json()["data"]["persisted"] is True

    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        dedicated = await env.client.post(RUNS, json={"case_ids": [case["id"]]})
    assert dedicated.status_code == 200, dedicated.text
    assert dedicated.json()["data"]["persisted"] is True
    assert dedicated.json()["data"]["run_id"] != run_id

    async with env.factory() as s:
        rows = (await s.execute(select(RetrievalEvalRun))).scalars().all()
        assert {row.id for row in rows} == {run_id, dedicated.json()["data"]["run_id"]}
        for row in rows:
            assert row.case_scope == "subset"
            assert row.case_ids == [case["id"]]
            assert row.created_by == USER_A
            assert row.organization_id == ORG_A
            assert row.counts == {"cases": 1, "evaluated": 1, "errors": 0}
            # 本用例的金标 DOC_A1 由 fake 检索排在 top-1，成功运行的文档级
            # recall/hit/MRR 必为 1.0；写死期望，避免恒真断言掩盖指标回归。
            assert row.metrics == {
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "document_hit_at_k": 1.0,
                "hit_at_k": 1.0,
            }


# ── 组织隔离 list/detail/export/trend ───────────────────────────────────


async def test_org_isolation_on_list_detail_export_and_trend(env):
    """跨组织 list 为空、detail/export 404、trend 无对方点。

    变异点：任一端点漏掉 organization_id 过滤或 get_run 只按主键查 → 对方 run
    出现在列表/趋势，或 detail/export 返回 200（含对方 query），本测试红。
    """
    _as(env)
    runs, fingerprint = await _seed_runs(env)
    run_id = runs[0].id

    _as(env, USER_B, ORG_B)
    assert (await env.client.get(RUNS)).json()["data"] == {"data": [], "total": 0}
    assert (await env.client.get(f"{RUNS}/trend")).json()["data"]["summary"]["runs"] == 0
    for path in (f"{RUNS}/{run_id}", f"{RUNS}/{run_id}/export"):
        response = await env.client.get(path)
        assert response.status_code == 404, response.text
        assert "secret-query" not in response.text
        assert "no such" not in response.text.lower()

    _as(env)
    detail = await env.client.get(f"{RUNS}/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["results"][0]["query"] == "secret-query-should-stay-in-org"
    listed = await env.client.get(RUNS)
    assert listed.json()["data"]["total"] == 3
    assert {row["id"] for row in listed.json()["data"]["data"]} == {r.id for r in runs}
    # 哈希过滤只返回同输入集合的运行（合法比较前提）。
    filtered = await env.client.get(RUNS, params={"case_set_hash": fingerprint})
    assert filtered.json()["data"]["total"] == 3
    assert (await env.client.get(RUNS, params={"case_set_hash": "f" * 64})).json()["data"][
        "total"
    ] == 0


# ── JSON / CSV 导出一致性 ───────────────────────────────────────────────


async def test_export_json_csv_consistency_and_audit_has_no_query(env):
    """JSON/CSV 同源：哈希/top_k/counts/metrics/results 一致；审计不记 query。

    变异点：CSV 改用另一套序列化（漏 case_set_hash 或改指标口径）→ 交叉断言红；
    export detail 写 query → sentinel 审计断言红；export 无审计 → action 断言红。
    """
    _as(env)
    runs, _ = await _seed_runs(env, count=1)
    run_id = runs[0].id
    sentinel = "secret-query-should-stay-in-org"

    json_resp = await env.client.get(f"{RUNS}/{run_id}/export", params={"format": "json"})
    assert json_resp.status_code == 200, json_resp.text
    assert json_resp.headers["content-type"].startswith("application/json")
    assert 'filename="eval-run-' in json_resp.headers["content-disposition"]
    payload = json.loads(json_resp.text)
    assert payload["schema_version"] == 1
    assert payload["export_type"] == "retrieval_eval_run"
    data = payload["data"]
    assert payload["case_set_hash"] == data["case_set_hash"]
    assert payload["top_k"] == data["top_k"] == 5
    assert data["results"][0]["query"] == sentinel

    csv_resp = await env.client.get(f"{RUNS}/{run_id}/export", params={"format": "csv"})
    assert csv_resp.status_code == 200, csv_resp.text
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == data["id"]
    assert row["case_set_hash"] == data["case_set_hash"]
    assert row["top_k"] == str(data["top_k"])
    assert json.loads(row["counts"]) == data["counts"]
    assert json.loads(row["metrics"]) == data["metrics"]
    assert json.loads(row["results"]) == data["results"]

    assert (
        await env.client.get(f"{RUNS}/{run_id}/export", params={"format": "xml"})
    ).status_code == 422
    actions = [call.kwargs["action"] for call in env.state.audit.await_args_list]
    assert "export_retrieval_eval_run" in actions
    for call in env.state.audit.await_args_list:
        assert sentinel not in call.kwargs["detail"]


# ── 失败运行与趋势聚合 ──────────────────────────────────────────────────


async def test_trend_excludes_failed_runs_from_metric_averages(env):
    """趋势：3 次运行含 1 次失败 → 成功 2 次的均值，失败数单独计数。

    变异点：把失败运行也纳入 recall 平均（None→0）会让 averages.recall_at_k
    从 0.6 变 0.4；漏掉组织过滤会掺入 B 组织点，summary.runs 漂移。
    """
    _as(env)
    runs, fingerprint = await _seed_runs(env, count=3)
    trend = await env.client.get(RUNS + "/trend", params={"case_set_hash": fingerprint})
    assert trend.status_code == 200, trend.text
    data = trend.json()["data"]
    assert [p["id"] for p in data["points"]] == [r.id for r in runs]
    assert data["summary"] == {
        "runs": 3,
        "succeeded_runs": 2,
        "failed_runs": 1,
        "averages": {
            "recall_at_k": 0.55,
            "mrr": 0.45,
            "document_hit_at_k": 0.6,
            "hit_at_k": 0.6,
        },
    }
    empty = await env.client.get(RUNS + "/trend", params={"case_set_hash": "e" * 64})
    assert empty.json()["data"]["summary"]["averages"]["recall_at_k"] is None


async def test_failed_run_persists_error_case_ids_and_counts(env):
    """检索失败的运行需完整保留 error_case_ids 与 errors 计数（不是 0 召回）。"""
    from unittest.mock import patch

    _as(env)
    case = await _case(env, query="boom-query")
    fake = await _fake_run(env, {}, errors=["boom-query"])
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        response = await env.client.post(RUNS, json={"case_ids": [case["id"]]})
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["counts"]["errors"] == 1
    assert report["error_case_ids"] == [case["id"]]
    assert report["results"][0]["status"] == "error"
    assert report["metrics"]["recall_at_k"] is None

    detail = await env.client.get(f"{RUNS}/{report['run_id']}")
    persisted = detail.json()["data"]
    assert persisted["counts"] == report["counts"]
    assert persisted["error_case_ids"] == report["error_case_ids"]
    assert persisted["metrics"] == report["metrics"]
    assert persisted["results"] == report["results"]


# ── RBAC / scope ────────────────────────────────────────────────────────


async def test_run_history_requires_auth_and_scope(env):
    """未认证 401；API Key 缺 knowledge:read → 403；有 scope → 200。

    变异点：新增端点漏挂 require_scope → 受限 key 仍能读历史，第二个断言红。
    """
    from app.core.security import get_current_user
    from app.main import app

    _as(env)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        for path, method in (
            (RUNS, "get"),
            (RUNS, "post"),
            (RUNS + "/trend", "get"),
            (f"{RUNS}/x", "get"),
            (f"{RUNS}/x/export", "get"),
        ):
            response = await getattr(env.client, method)(path)
            assert response.status_code == 401, (path, response.text)
    finally:
        app.dependency_overrides[get_current_user] = lambda: env.state.user

    env.state.user.api_key_scopes = ["chat:read"]
    try:
        for path in (RUNS, RUNS + "/trend", f"{RUNS}/x", f"{RUNS}/x/export"):
            response = await env.client.get(path)
            assert response.status_code == 403, (path, response.text)
    finally:
        env.state.user.api_key_scopes = None


# ── 迁移与模型注册 ──────────────────────────────────────────────────────


def test_models_registry_contains_retrieval_eval_run():
    """干净进程只 import app.models：run 表注册、FK/索引齐全。

    变异点：从 app/models/__init__.py 移除导入/导出 → 子进程元数据无此表。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    script = r"""
import json
import app.models
from app.core.database import Base
t = Base.metadata.tables.get("retrieval_eval_run")
print("RESULT_JSON=" + json.dumps({
    "exists": t is not None,
    "columns": sorted(c.name for c in t.columns) if t is not None else [],
    "fks": sorted(fk.target_fullname for fk in t.foreign_keys) if t is not None else [],
    "indexes": [{"name": idx.name, "columns": [c.name for c in idx.columns]}
                for idx in t.indexes] if t is not None else [],
}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(
        [line for line in proc.stdout.splitlines() if line.startswith("RESULT_JSON=")][-1].split(
            "RESULT_JSON=", 1
        )[1]
    )
    assert payload["exists"] is True, payload
    assert set(payload["columns"]) >= {
        "id",
        "organization_id",
        "created_by",
        "top_k",
        "case_ids",
        "case_scope",
        "case_set_hash",
        "counts",
        "metrics",
        "error_case_ids",
        "results",
        "created_at",
    }
    assert set(payload["fks"]) >= {"organizations.id", "users.id"}
    assert any(
        idx["name"] == "ix_retrieval_eval_run_org_created"
        and idx["columns"] == ["organization_id", "created_at"]
        for idx in payload["indexes"]
    )


def test_035_migration_single_head_schema_and_downgrade(tmp_path, monkeypatch):
    """035 接 034、单 head；迁移后表列/FK/索引齐全；downgrade 随表删除索引。

    变异点：down_revision 改错 → heads 断言红；迁移漏 case_scope/索引 → schema
    断言红；downgrade 先 drop_index → 字符串断言红（MySQL 1553）。
    """
    import sqlite3
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from alembic import command
    from app.core.config import settings

    db_path = Path(str(tmp_path)) / "eval_run_migration.db"
    backend = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("035_retrieval_eval_run").down_revision == (
        "034_retrieval_eval_case"
    )
    command.upgrade(cfg, "head")

    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_eval_run)")}
        fks = conn.execute("PRAGMA foreign_key_list(retrieval_eval_run)").fetchall()
        indexes = conn.execute("PRAGMA index_list(retrieval_eval_run)").fetchall()
        index_cols = [
            tuple(col[2] for col in conn.execute(f"PRAGMA index_info({row[1]})")) for row in indexes
        ]
    assert {
        "id",
        "organization_id",
        "created_by",
        "top_k",
        "case_ids",
        "case_scope",
        "case_set_hash",
        "counts",
        "metrics",
        "error_case_ids",
        "results",
        "created_at",
    } <= cols
    assert {row[2] for row in fks} >= {"organizations", "users"}
    assert {row[3] for row in fks} >= {"organization_id", "created_by"}
    assert ("organization_id", "created_at") in index_cols

    migration = (backend / "alembic" / "versions" / "035_retrieval_eval_run.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(TABLE)" in downgrade
    assert "op.drop_index(INDEX" not in downgrade


def test_migration_chain_has_single_head_including_039_review_template(tmp_path, monkeypatch):
    """迁移链必须单头，且 039 是当前 head、接在 038 之后。

    本断言原先挂在 035 的 schema 用例里——那个用例守的是「035 表结构」，
    与「最新 head 是谁」无关；每次新增迁移都会迫使其连带改名/改断言。
    单独成例后，新增迁移只需改这里的一处（或本断言随 head 一同前移）。
    批次四集成：036_chunk_override 保留，索引错误码迁移线性化为 037；
    批次五：038_eval_case_dedup 保留，review template 迁移线性化为 039。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    assert heads == {"039_review_template"}
    assert script.get_revision("038_eval_case_dedup").down_revision == ("037_index_error_codes")
    assert script.get_revision("039_review_template").down_revision == ("038_eval_case_dedup")
