"""答案质量评测 API（真实 SQLite + ASGI）：运行/历史/详情/趋势/RBAC/隐私/失败。

判别性覆盖（每个用例标题写出变异点）：
- fake 运行确定性落库；列表/详情与聚合同源；
- 原始 question/answer/context 绝不入 DB 行或 API 响应（变异：把原始
  case 写进 results → sentinel 断言红）；
- judge 不可用 → 502 + 失败运行 NULL 分数（变异：把失败写成 0 分 → 红）；
- judge 成功复用评分核心；理由有界；同配置重复提交幂等；
- 跨组织 404 且不回显内容；趋势失败计数但不污染均值；
- 读取要 knowledge:read，写入要 knowledge:write；未认证 401。
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.exceptions import ServiceUnavailableError
from app.models.answer_quality_eval import AnswerQualityEvalRun
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.user import User
from app.rag.errors import EvalJudgeContractError, EvalJudgeUnavailableError

ORG = 1
OTHER_ORG = 2
USER_A = 1
USER_B = 2  # 其他组织
USER_C = 3  # 同组织另一用户
RUNS = "/api/v1/eval/answer-quality/runs"

SENTINEL_Q = "SECRET-QUESTION-别泄露-9f3a"
SENTINEL_A = "SECRET-ANSWER-别泄露-7b2c"
SENTINEL_C = "SECRET-CONTEXT-别泄露-1d4e-这是一段足够长的上下文正文"


def _case(case_id="c1", *, f=0.9, r=0.8, c=0.7):
    return {
        "case_id": case_id,
        "question": f"{SENTINEL_Q}-{case_id}",
        "answer": f"{SENTINEL_A}-{case_id}",
        "context": [{"doc_id": f"doc-{case_id}", "text": f"{SENTINEL_C}-{case_id}"}],
        "fake_scores": {"faithfulness": f, "relevancy": r, "context_precision": c},
    }


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'answer-quality-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=0))
        for uid, org in ((USER_A, ORG), (USER_B, OTHER_ORG), (USER_C, ORG)):
            db.add(
                User(
                    id=uid,
                    username=f"user{uid}",
                    email=f"user{uid}@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=org,
                )
            )
        await db.commit()
    yield maker
    await engine.dispose()


class _Ctx:
    def __init__(self, user, factory):
        self.user = user
        self.factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _user():
            return self.user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://aq")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(*, uid=USER_A, org=ORG, superuser=True, scopes=None):
    user = SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="admin" if superuser else "user",
    )
    if scopes is not None:
        user.api_key_scopes = scopes
    return user


def _grant(*permissions: PermissionType):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {permission.value for permission in permissions}

    return _grant_all


async def _post_fake(ac, cases=None):
    return await ac.post(
        RUNS,
        json={"mode": "fake", "cases": cases if cases is not None else [_case()]},
    )


async def _rows(factory):
    async with factory() as session:
        return (await session.execute(select(AnswerQualityEvalRun))).scalars().all()


# ── fake 确定性 / 列表 / 详情 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_run_is_deterministic_and_list_detail_match(factory, monkeypatch):
    """fake 回放给定分数：聚合=逐条均值；列表与详情同源。

    变异点：fake 改调模型/随机分数 → 固定聚合断言红；列表漏指标 → data 断言红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    cases = [_case("c1", f=0.9, r=0.8, c=0.7), _case("c2", f=0.3, r=0.4, c=0.5)]
    async with _Ctx(_user(), factory) as ac:
        created = await _post_fake(ac, cases)
        assert created.status_code == 200, created.text
        data = created.json()["data"]
        assert data["status"] == "completed"
        assert data["mode"] == "fake"
        assert data["judge_model"] == "fake"
        assert data["prompt_version"]
        assert data["case_count"] == 2
        assert data["aggregate"]["faithfulness"] == pytest.approx(0.6)
        assert data["aggregate"]["relevancy"] == pytest.approx(0.6)
        assert data["aggregate"]["context_precision"] == pytest.approx(0.6)
        assert data["aggregate"]["cases_scored"] == 2
        assert [r["case_id"] for r in data["results"]] == ["c1", "c2"]
        assert data["results"][0]["metrics"] == {
            "faithfulness": 0.9,
            "relevancy": 0.8,
            "context_precision": 0.7,
        }

        listed = await ac.get(RUNS)
        assert listed.status_code == 200, listed.text
        payload = listed.json()["data"]
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == data["id"]
        assert (
            payload["items"][0]["metrics"]
            == data["metrics"]
            == {
                "faithfulness": 0.6,
                "relevancy": 0.6,
                "context_precision": 0.6,
            }
        )

        detail = await ac.get(f"{RUNS}/{data['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["results"] == data["results"]


@pytest.mark.asyncio
async def test_raw_question_answer_context_never_persisted_or_returned(factory, monkeypatch):
    """DB 行与 API 响应都不得含原始 query/answer/context 文本。

    变异点：results 写入 case 原文（或聚合里带 context）→ sentinel 断言红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _post_fake(ac)
        assert created.status_code == 200, created.text
        body = created.text
        assert SENTINEL_Q not in body
        assert SENTINEL_A not in body
        assert SENTINEL_C not in body

        run_id = created.json()["data"]["id"]
        listed = (await ac.get(RUNS)).text
        detail = (await ac.get(f"{RUNS}/{run_id}")).text
        for text in (listed, detail):
            assert SENTINEL_Q not in text
            assert SENTINEL_A not in text
            assert SENTINEL_C not in text

        # 来源只保留哈希 + 脱敏预览。
        result = created.json()["data"]["results"][0]
        assert len(result["source_refs"]) == 1
        assert len(result["source_refs"][0]["ref_sha256"]) == 64
        assert result["source_refs"][0]["masked_preview"] != SENTINEL_C

    rows = await _rows(factory)
    assert len(rows) == 1
    serialized = json.dumps([rows[0].aggregate, rows[0].results], ensure_ascii=False)
    assert SENTINEL_Q not in serialized
    assert SENTINEL_A not in serialized
    assert SENTINEL_C not in serialized


# ── judge 成功 / 失败语义 / 幂等 ─────────────────────────────────────────


class _FakeEvaluation:
    faithfulness = 0.95
    relevancy = 0.85
    context_precision = 0.75
    details = {
        "faithfulness_reason": "依据充分 " + "x" * 1000,
        "relevancy_reason": "直接回答 " + "y" * 1000,
        "context_precision_reason": "文档相关 " + "z" * 1000,
    }


class _FakeEvaluator:
    calls = 0

    def __init__(self, client=None, model=None):
        self.client = client
        self.model = model

    async def evaluate(self, question, answer, context):
        type(self).calls += 1
        return _FakeEvaluation()


class _UnavailableEvaluator:
    async def evaluate(self, question, answer, context):
        raise EvalJudgeUnavailableError("judge down")


@pytest.mark.asyncio
async def test_judge_success_reuses_core_bounds_reasons_and_is_idempotent(factory, monkeypatch):
    """judge 复用评分核心；理由截断 300；同配置重复提交返回同一运行。

    变异点：理由不截断 → 长度断言红；去掉幂等回读 → 列表 total=2 且
    evaluator 调用翻倍，本测试红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    _FakeEvaluator.calls = 0

    async def _resolver(_db, _user):
        return _FakeEvaluator(), "test-judge-model"

    monkeypatch.setattr(
        "app.api.v1.endpoints.answer_quality_eval._judge_evaluator_and_model", _resolver
    )
    payload = {
        "mode": "judge",
        "cases": [
            {
                "case_id": "j1",
                "question": f"{SENTINEL_Q}-j1",
                "answer": f"{SENTINEL_A}-j1",
                "context": [{"text": f"{SENTINEL_C}-j1"}],
            }
        ],
    }
    async with _Ctx(_user(), factory) as ac:
        first = await ac.post(RUNS, json=payload)
        assert first.status_code == 200, first.text
        data = first.json()["data"]
        assert data["judge_model"] == "test-judge-model"
        assert data["aggregate"]["faithfulness"] == 0.95
        assert data["aggregate"]["relevancy"] == 0.85
        assert data["aggregate"]["context_precision"] == 0.75
        for metric in ("faithfulness", "relevancy", "context_precision"):
            assert len(data["results"][0]["reasons"][metric]) == 300
        assert _FakeEvaluator.calls == 1

        second = await ac.post(RUNS, json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["data"]["id"] == data["id"]
        assert _FakeEvaluator.calls == 1, "幂等回放不得再次调用评委"
        assert (await ac.get(RUNS)).json()["data"]["total"] == 1


class _ContractEvaluator:
    async def evaluate(self, question, answer, context):
        raise EvalJudgeContractError("judge output unparseable")


class _LeakyReasonEvaluator:
    async def evaluate(self, question, answer, context):
        return SimpleNamespace(
            faithfulness=0.9,
            relevancy=0.8,
            context_precision=0.7,
            details={
                "faithfulness_reason": f"答案提到 {answer}",
                "relevancy_reason": f"上下文 {context[0]['text'] if context else ''}",
                "context_precision_reason": "normal reason",
            },
        )


@pytest.mark.asyncio
async def test_judge_contract_violation_is_failed_run_not_zero(factory, monkeypatch):
    """评委输出不可解析 -> failed + JUDGE_CONTRACT_VIOLATION，不是 completed 0 分。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    async def _resolver(_db, _user):
        return _ContractEvaluator(), "test-judge-model"

    monkeypatch.setattr(
        "app.api.v1.endpoints.answer_quality_eval._judge_evaluator_and_model", _resolver
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "judge",
                "cases": [{"case_id": "c1", "question": "q", "answer": "a", "context": []}],
            },
        )
        assert response.status_code == 502, response.text
        assert response.json()["error_code"] == "JUDGE_CONTRACT_VIOLATION"
        run_id = response.json()["detail"]["run_id"]
        data = (await ac.get(f"{RUNS}/{run_id}")).json()["data"]
        assert data["status"] == "failed"
        assert data["aggregate"] is None and data["results"] is None

    rows = await _rows(factory)
    assert len(rows) == 1
    assert rows[0].aggregate is None and rows[0].results is None


@pytest.mark.asyncio
async def test_judge_reason_quoting_input_is_dropped(factory, monkeypatch):
    """评委理由转述原文 -> 整条理由丢弃，DB/响应都不得含 sentinel。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    async def _resolver(_db, _user):
        return _LeakyReasonEvaluator(), "test-judge-model"

    monkeypatch.setattr(
        "app.api.v1.endpoints.answer_quality_eval._judge_evaluator_and_model", _resolver
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "judge",
                "cases": [
                    {
                        "case_id": "c1",
                        "question": f"{SENTINEL_Q}-c1",
                        "answer": f"{SENTINEL_A}-c1",
                        "context": [{"text": f"{SENTINEL_C}-c1"}],
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        body = response.text
        for sentinel in (SENTINEL_Q, SENTINEL_A, SENTINEL_C):
            assert sentinel not in body
        reasons = response.json()["data"]["results"][0]["reasons"]
        assert "faithfulness" not in reasons
        assert "relevancy" not in reasons

    rows = await _rows(factory)
    serialized = json.dumps(rows[0].results, ensure_ascii=False)
    for sentinel in (SENTINEL_Q, SENTINEL_A, SENTINEL_C):
        assert sentinel not in serialized


@pytest.mark.asyncio
async def test_masked_preview_has_no_raw_prefix_and_counts_chars(factory, monkeypatch):
    """预览不得保留原文前缀（9 字符秘密曾泄露 8/9），只给固定占位 + 长度。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "fake",
                "cases": [
                    {
                        "case_id": "c1",
                        "question": "q",
                        "answer": "a",
                        "context": [{"text": "123456789"}],
                        "fake_scores": {
                            "faithfulness": 0.9,
                            "relevancy": 0.8,
                            "context_precision": 0.7,
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        ref = response.json()["data"]["results"][0]["source_refs"][0]
        assert ref["masked_preview"] == "[redacted]"
        assert ref["char_count"] == 9
        assert "12345678" not in response.text


@pytest.mark.asyncio
async def test_context_non_string_values_are_coerced_not_500(factory, monkeypatch):
    """context 数值字段必须在调用评委前归一，不能事后 500。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "fake",
                "cases": [
                    {
                        "case_id": "c1",
                        "question": "q",
                        "answer": "a",
                        "context": [{"text": 123}],
                        "fake_scores": {
                            "faithfulness": 0.9,
                            "relevancy": 0.8,
                            "context_precision": 0.7,
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        nested = await ac.post(
            RUNS,
            json={
                "mode": "fake",
                "cases": [
                    {
                        "case_id": "c2",
                        "question": "q",
                        "answer": "a",
                        "context": [{"text": {"nested": "x"}}],
                        "fake_scores": {
                            "faithfulness": 0.9,
                            "relevancy": 0.8,
                            "context_precision": 0.7,
                        },
                    }
                ],
            },
        )
        assert nested.status_code == 400, nested.text


@pytest.mark.asyncio
async def test_trend_returns_newest_runs_not_oldest(factory, monkeypatch):
    """趋势先取最新 N 条再反转；超过 limit 时不得丢掉最新运行。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with factory() as db:
        for index in range(3):
            ts = base + timedelta(minutes=index)
            db.add(
                AnswerQualityEvalRun(
                    id=f"run-{index}",
                    organization_id=ORG,
                    user_id=USER_A,
                    dataset_hash=str(index) * 64,
                    judge_model="fake",
                    prompt_version="v1",
                    mode="fake",
                    status="completed",
                    case_count=1,
                    aggregate={
                        "faithfulness": 0.1 * index,
                        "relevancy": 0.1 * index,
                        "context_precision": 0.1 * index,
                    },
                    results=[],
                    created_by=USER_A,
                    created_at=ts,
                    completed_at=ts,
                )
            )
        await db.commit()
    async with _Ctx(_user(), factory) as ac:
        response = await ac.get(f"{RUNS}/trend?limit=2")
        assert response.status_code == 200, response.text
        points = response.json()["data"]["points"]
        assert [point["id"] for point in points] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_llm_not_configured_returns_503_without_run_row(factory, monkeypatch):
    """LLM 未配置是请求前配置错误：503 且不产生运行行。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    async def _resolver(_db, _user):
        raise ServiceUnavailableError("LLM 服务未配置", error_code="LLM_NOT_CONFIGURED")

    monkeypatch.setattr(
        "app.api.v1.endpoints.answer_quality_eval._judge_evaluator_and_model", _resolver
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "judge",
                "cases": [{"case_id": "c1", "question": "q", "answer": "a", "context": []}],
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["error_code"] == "LLM_NOT_CONFIGURED"
    assert await _rows(factory) == []


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_raw_input(factory, monkeypatch):
    """422 响应只回显 loc/msg/type，不能把原始答案正文带回客户端。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "fake",
                "cases": [
                    {
                        "case_id": "c1",
                        "question": "q",
                        "answer": SENTINEL_A * 2000,
                        "context": [],
                    }
                ],
            },
        )
        assert response.status_code == 422, response.text
        assert SENTINEL_A not in response.text


@pytest.mark.asyncio
async def test_judge_unavailable_persists_failed_run_with_null_scores(factory, monkeypatch):
    """评委不可用 → 502 + failed 行；aggregate/results 为 NULL（不是 0 分）。

    变异点：失败运行写 aggregate={"faithfulness":0,...} → NULL/None 断言红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    async def _resolver(_db, _user):
        return _UnavailableEvaluator(), "test-judge-model"

    monkeypatch.setattr(
        "app.api.v1.endpoints.answer_quality_eval._judge_evaluator_and_model", _resolver
    )
    async with _Ctx(_user(), factory) as ac:
        response = await ac.post(
            RUNS,
            json={
                "mode": "judge",
                "cases": [
                    {
                        "case_id": "j1",
                        "question": f"{SENTINEL_Q}-j1",
                        "answer": f"{SENTINEL_A}-j1",
                        "context": [],
                    }
                ],
            },
        )
        assert response.status_code == 502, response.text
        error = response.json()
        assert error["error_code"] == "JUDGE_UNAVAILABLE"
        run_id = error["detail"]["run_id"]

        detail = await ac.get(f"{RUNS}/{run_id}")
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert data["status"] == "failed"
        assert data["error_code"] == "JUDGE_UNAVAILABLE"
        assert data["aggregate"] is None
        assert data["results"] is None
        assert data["metrics"] == {
            "faithfulness": None,
            "relevancy": None,
            "context_precision": None,
        }

    rows = await _rows(factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "failed"
    assert row.aggregate is None
    assert row.results is None
    assert row.error_code == "JUDGE_UNAVAILABLE"
    assert row.completed_at is not None
    # 绝不把「测量失败」写成 0 分。
    assert row.aggregate != {"faithfulness": 0, "relevancy": 0, "context_precision": 0}


# ── 组织隔离 / 过滤 / 趋势 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_org_isolation_on_list_detail_and_trend(factory, monkeypatch):
    """跨组织 list 为空、detail 404、trend 无对方点，且不回显原文。

    变异点：get_run 只按主键查 → detail 200 且 sentinel 泄漏，本测试红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(uid=USER_A, org=ORG), factory) as ac:
        created = await _post_fake(ac)
        assert created.status_code == 200, created.text
        run_id = created.json()["data"]["id"]

    async with _Ctx(_user(uid=USER_B, org=OTHER_ORG), factory) as ac:
        assert (await ac.get(RUNS)).json()["data"] == {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
        }
        assert (await ac.get(f"{RUNS}/trend")).json()["data"]["summary"]["runs"] == 0
        response = await ac.get(f"{RUNS}/{run_id}")
        assert response.status_code == 404, response.text
        assert SENTINEL_A not in response.text
        assert SENTINEL_C not in response.text

    async with _Ctx(_user(uid=USER_A, org=ORG), factory) as ac:
        detail = await ac.get(f"{RUNS}/{run_id}")
        assert detail.status_code == 200, detail.text
        assert (await ac.get(RUNS)).json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_list_filters_user_status_and_date(factory, monkeypatch):
    """用户/状态/日期过滤与非法区间 422。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(uid=USER_A, org=ORG), factory) as ac:
        assert (await _post_fake(ac, [_case("a1")])).status_code == 200
    async with _Ctx(_user(uid=USER_C, org=ORG), factory) as ac:
        assert (await _post_fake(ac, [_case("c1")])).status_code == 200

    async with _Ctx(_user(uid=USER_A, org=ORG), factory) as ac:
        mine = (await ac.get(RUNS, params={"user_id": USER_A})).json()["data"]
        assert mine["total"] == 1
        assert mine["items"][0]["user_id"] == USER_A
        other = (await ac.get(RUNS, params={"user_id": USER_C})).json()["data"]
        assert other["total"] == 1
        assert other["items"][0]["user_id"] == USER_C
        assert (await ac.get(RUNS, params={"status": "failed"})).json()["data"]["total"] == 0
        future = (await ac.get(RUNS, params={"date_from": "2099-01-01"})).json()["data"]
        assert future["total"] == 0
        assert (
            await ac.get(RUNS, params={"date_from": "2026-02-01", "date_to": "2026-01-01"})
        ).status_code == 400, "服务层校验失败映射为 400（AppError 信封），不是静默空列表"


async def _seed_trend_rows(factory):
    base = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    async with factory() as session:
        session.add_all(
            [
                AnswerQualityEvalRun(
                    id="run-1",
                    organization_id=ORG,
                    user_id=USER_A,
                    dataset_hash="a" * 64,
                    judge_model="fake",
                    prompt_version="rag-eval-v1",
                    mode="fake",
                    status="completed",
                    case_count=1,
                    aggregate={
                        "faithfulness": 0.9,
                        "relevancy": 0.8,
                        "context_precision": 0.7,
                        "cases_scored": 1,
                    },
                    results=[],
                    created_by=USER_A,
                    created_at=base,
                    completed_at=base,
                ),
                AnswerQualityEvalRun(
                    id="run-2",
                    organization_id=ORG,
                    user_id=USER_A,
                    dataset_hash="b" * 64,
                    judge_model="fake",
                    prompt_version="rag-eval-v1",
                    mode="fake",
                    status="completed",
                    case_count=1,
                    aggregate={
                        "faithfulness": 0.5,
                        "relevancy": 0.4,
                        "context_precision": 0.3,
                        "cases_scored": 1,
                    },
                    results=[],
                    created_by=USER_A,
                    created_at=base + timedelta(hours=1),
                    completed_at=base + timedelta(hours=1),
                ),
                AnswerQualityEvalRun(
                    id="run-3",
                    organization_id=ORG,
                    user_id=USER_A,
                    dataset_hash="c" * 64,
                    judge_model="fake",
                    prompt_version="rag-eval-v1",
                    mode="judge",
                    status="failed",
                    case_count=1,
                    aggregate=None,
                    results=None,
                    error_code="JUDGE_UNAVAILABLE",
                    created_by=USER_A,
                    created_at=base + timedelta(hours=2),
                    completed_at=base + timedelta(hours=2),
                ),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_trend_math_excludes_failed_runs_from_averages(factory, monkeypatch):
    """3 次运行含 1 次失败 → 成功 2 次均值；失败计数独立。

    变异点：失败运行按 0 分计入 → averages 从 0.7/0.6/0.5 变 0.4667/...，红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    await _seed_trend_rows(factory)
    async with _Ctx(_user(uid=USER_A, org=ORG), factory) as ac:
        response = await ac.get(f"{RUNS}/trend")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert [point["id"] for point in data["points"]] == ["run-1", "run-2", "run-3"]
        assert data["points"][2]["metrics"] == {
            "faithfulness": None,
            "relevancy": None,
            "context_precision": None,
        }
        assert data["summary"] == {
            "runs": 3,
            "succeeded_runs": 2,
            "failed_runs": 1,
            "averages": {
                "faithfulness": 0.7,
                "relevancy": 0.6,
                "context_precision": 0.5,
            },
        }
        failed_only = (await ac.get(RUNS + "/trend", params={"status": "failed"})).json()["data"]
        assert failed_only["summary"]["runs"] == 1
        assert failed_only["summary"]["averages"] == {
            "faithfulness": None,
            "relevancy": None,
            "context_precision": None,
        }


# ── RBAC / API Key scope ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rbac_and_scope_matrix(factory, monkeypatch):
    """未认证 401；读 key 不能写；写 key 不能读；JWT 全量可读写。

    变异点：POST 漏挂 knowledge:write → 只读 key 也能触发评委，断言红。
    """
    from app.main import app

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    ctx = _Ctx(_user(), factory)
    async with ctx as ac:
        app.dependency_overrides.pop(get_current_user, None)
        try:
            for method, path in (
                ("get", RUNS),
                ("get", RUNS + "/trend"),
                ("get", f"{RUNS}/x"),
                ("post", RUNS),
            ):
                response = await getattr(ac, method)(path)
                assert response.status_code == 401, (method, path, response.text)
        finally:
            app.dependency_overrides[get_current_user] = lambda: ctx.user

        ctx.user = _user(scopes=["knowledge:read"])
        assert (await ac.get(RUNS)).status_code == 200
        assert (await ac.get(RUNS + "/trend")).status_code == 200
        denied_write = await _post_fake(ac)
        assert denied_write.status_code == 403, denied_write.text

        ctx.user = _user(scopes=["knowledge:write"])
        assert (await ac.get(RUNS)).status_code == 403
        allowed_write = await _post_fake(ac, [_case("scope-write")])
        assert allowed_write.status_code == 200, allowed_write.text


@pytest.mark.asyncio
async def test_non_superuser_without_permission_is_denied(factory, monkeypatch):
    """非超管缺 SEARCH_KNOWLEDGE_BASE → 读/写都 403（RBAC 分支，不只是 scope）。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(),
    )
    async with _Ctx(_user(superuser=False), factory) as ac:
        assert (await ac.get(RUNS)).status_code == 403
        assert (await _post_fake(ac)).status_code == 403
