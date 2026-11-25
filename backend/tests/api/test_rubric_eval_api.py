"""Rubric 评测 API（真实 SQLite + ASGI）：CRUD / 版本 / diff / 归档 / 运行 / RBAC / scope。

判别性覆盖（每个用例标题写出变异点）：

- 读取要 knowledge:read + SEARCH_KNOWLEDGE_BASE，写入要 knowledge:write；
  去掉 scope/RBAC 依赖或放宽读写分离 → 对应用例红；
- 跨组织评分表/版本/运行一律 404，列表不泄露（get_rubric 去掉 org 校验 → 红）；
- 版本行不可变：新增版本后旧版本内容与哈希不变；
- 运行：fake 模式确定性得分；judge 模式评委不可用 → 502 + 失败运行已落库
  （回查详情可见 error_code）；校准缺失金标 → aggregate.calibration 为 null；
- 归档后写操作 409；非法载荷 422；运行端点写入权限（read-only key → 403）。
"""

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.user import User
from app.services import rubric_eval_service as rubric_service

ORG = 1
OTHER_ORG = 2
USER_ID = 1


ITEMS = [
    {"key": "grounded", "question": "回答是否与资料一致？", "weight": 3, "strictness": "neutral"},
    {"key": "cites", "question": "是否给出可核对依据？", "weight": 1, "strictness": "neutral"},
]

FAKE_CASES = [
    {
        "case_id": "c1",
        "name": "好答案",
        "answer": "根据制度第3条，付款期限为30天。",
        "fake_verdicts": {"grounded": "yes", "cites": "yes"},
    },
    {
        "case_id": "c2",
        "name": "差答案",
        "answer": "可能是三十天。",
        "fake_verdicts": {"grounded": "no", "cites": "no"},
    },
]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rubric-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=0))
        for uid, org in ((USER_ID, ORG), (2, OTHER_ORG)):
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
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://rubric")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(*, org=ORG, superuser=True, scopes=None):
    user = SimpleNamespace(
        id=USER_ID,
        username="tester",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="admin" if superuser else "user",
    )
    if scopes is not None:
        # 与 require_scope 的判定同源：容器类型 = 真实 API Key 的 scope 集合。
        user.api_key_scopes = scopes
    return user


def _grant(*permissions: PermissionType):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {permission.value for permission in permissions}

    return _grant_all


def _grant_scoped(*permissions: PermissionType):
    async def _grant_for_superuser(_db, user_obj, organization_id=None):
        # 非超管替身：按注入的 scope 决定可见权限（超管分支不查库）。
        if getattr(user_obj, "is_superuser", False):
            return {permission.value for permission in permissions}
        return {permission.value for permission in permissions}

    return _grant_for_superuser


async def _create_rubric(ac, *, name="回答质量", items=None) -> dict:
    payload: dict = {"name": name}
    if items is not None:
        payload["items"] = items
    r = await ac.post("/api/v1/eval/rubrics", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ── CRUD / 版本 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_list_get_and_version_flow(factory, monkeypatch):
    """创建（带首版）→ 列表 → 详情 → 加版本 → 版本列表/单版/diff。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        assert created["latest_version"] == 1
        assert created["status"] == "active"
        assert created["version"]["version_no"] == 1
        assert created["version"]["item_count"] == 2
        assert created["version"]["intact"] is True

        listed = await ac.get("/api/v1/eval/rubrics")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 1

        detail = await ac.get(f"/api/v1/eval/rubrics/{created['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["version"]["items"][0]["key"] == "grounded"

        second = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/versions",
            json={
                "items": [{**ITEMS[0], "weight": 5}, ITEMS[1]],
                "change_note": "提高权重",
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["data"]["version_no"] == 2

        versions = await ac.get(f"/api/v1/eval/rubrics/{created['id']}/versions")
        rows = versions.json()["data"]["items"]
        assert [row["version_no"] for row in rows] == [2, 1]
        assert all("items" not in row for row in rows)

        # v1 不可变：哈希与项内容都不随 v2 变化。
        v1 = await ac.get(f"/api/v1/eval/rubrics/{created['id']}/versions/1")
        assert v1.json()["data"]["payload_sha256"] == created["version"]["payload_sha256"]
        assert v1.json()["data"]["items"][0]["weight"] == 3

        diff = await ac.get(
            f"/api/v1/eval/rubrics/{created['id']}/diff",
            params={"base_version": 1, "target_version": 2},
        )
        assert diff.status_code == 200, diff.text
        data = diff.json()["data"]
        assert data["identical"] is False
        assert data["summary"]["changed"] == 1
        assert data["changed"][0]["fields"]["weight"] == {"base": 3, "target": 5}

        missing = await ac.get(
            f"/api/v1/eval/rubrics/{created['id']}/diff",
            params={"base_version": 1, "target_version": 99},
        )
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_invalid_payload_is_422(factory, monkeypatch):
    """schema 层拦非法输入：非法 key/空问题/权重越界/未知严格度/空数组/超长名称。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        for payload in (
            {"name": "x", "items": []},
            {"name": "x", "items": [{"key": "Bad Key", "question": "q"}]},
            {"name": "x", "items": [{"key": "ok", "question": "   "}]},
            {"name": "x", "items": [{"key": "ok", "question": "q", "weight": 0}]},
            {"name": "x", "items": [{"key": "ok", "question": "q", "weight": 11}]},
            {"name": "x", "items": [{"key": "ok", "question": "q", "strictness": "sloppy"}]},
            {"name": "x" * 101},
            {"name": "x", "change_note": "无首版却写变更说明"},
        ):
            r = await ac.post("/api/v1/eval/rubrics", json=payload)
            assert r.status_code == 422, (payload, r.text)


@pytest.mark.asyncio
async def test_duplicate_name_and_archive_conflicts(factory, monkeypatch):
    """重名 409；归档后新增版本/归档自身 409；更新名称冲突 409。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        dup = await ac.post("/api/v1/eval/rubrics", json={"name": "回答质量", "items": ITEMS})
        assert dup.status_code == 409
        assert dup.json()["error_code"] == "EVAL_RUBRIC_NAME_CONFLICT"

        archived = await ac.post(f"/api/v1/eval/rubrics/{created['id']}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["data"]["status"] == "archived"

        version_after = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/versions", json={"items": ITEMS}
        )
        assert version_after.status_code == 409
        assert version_after.json()["error_code"] == "EVAL_RUBRIC_ARCHIVED"

        again = await ac.post(f"/api/v1/eval/rubrics/{created['id']}/archive")
        assert again.status_code == 409

        update = await ac.put(f"/api/v1/eval/rubrics/{created['id']}", json={"name": "改名"})
        assert update.status_code == 409


@pytest.mark.asyncio
async def test_cross_org_access_is_404(factory, monkeypatch):
    """跨组织评分表/版本/运行一律 404；列表只返回本组织。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(org=OTHER_ORG), factory) as ac:
        foreign = await _create_rubric(ac, name="别家的评分表", items=ITEMS)
    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get("/api/v1/eval/rubrics", params={"status": "all"})
        assert listed.json()["data"]["total"] == 0
        for path in (
            f"/api/v1/eval/rubrics/{foreign['id']}",
            f"/api/v1/eval/rubrics/{foreign['id']}/versions",
            f"/api/v1/eval/rubrics/{foreign['id']}/versions/1",
        ):
            r = await ac.get(path)
            assert r.status_code == 404, (path, r.text)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{foreign['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert run.status_code == 404


# ── 运行 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_fake_mode_and_run_history(factory, monkeypatch):
    """fake 运行：得分/逐问题通过率/null 校准；列表与详情可回查；数据集哈希稳定。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert run.status_code == 200, run.text
        data = run.json()["data"]
        assert data["status"] == "completed"
        assert data["mode"] == "fake"
        assert data["judge_model"] == rubric_service.FAKE_JUDGE_MODEL
        assert data["rubric_version_no"] == 1
        assert data["aggregate"]["score"] == 0.5  # c1=1.0, c2=0.0
        assert data["aggregate"]["calibration"] is None
        by_key = {row["key"]: row for row in data["aggregate"]["questions"]}
        assert by_key["grounded"]["pass_rate"] == 0.5
        assert [row["case_id"] for row in data["results"]] == ["c1", "c2"]

        listed = await ac.get("/api/v1/eval/runs", params={"rubric_id": created["id"]})
        assert listed.status_code == 200, listed.text
        items = listed.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["id"] == data["id"]
        assert items[0]["score"] == 0.5
        assert items[0]["calibration_available"] is False

        detail = await ac.get(f"/api/v1/eval/runs/{data['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["dataset_hash"] == data["dataset_hash"]

        # 同输入再跑一次：数据集哈希与聚合分完全一致（确定性）。
        again = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert again.json()["data"]["dataset_hash"] == data["dataset_hash"]
        assert again.json()["data"]["aggregate"] == data["aggregate"]


@pytest.mark.asyncio
async def test_run_with_gold_reports_calibration(factory, monkeypatch):
    """带人工金标的运行：aggregate.calibration 给出一致率与混淆矩阵。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    cases = [dict(case) for case in FAKE_CASES]
    cases[0]["gold_verdicts"] = {"grounded": "yes", "cites": "yes"}
    cases[1]["gold_verdicts"] = {"grounded": "no", "cites": "yes"}
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": cases},
        )
        assert run.status_code == 200, run.text
        calibration = run.json()["data"]["aggregate"]["calibration"]
        assert calibration["pairs_compared"] == 4
        assert calibration["pairs_agreed"] == 3  # c2.cites 不一致
        assert calibration["confusion"]["no"]["yes"] == 1
        listed = await ac.get("/api/v1/eval/runs")
        assert listed.json()["data"]["items"][0]["calibration_available"] is True


@pytest.mark.asyncio
async def test_judge_failure_is_502_and_run_is_persisted(factory, monkeypatch):
    """judge 评委不可用 → 502 + 机器码；失败运行已落库、无任何分数。

    变异点：把失败运行跳过持久化或返回 200 空分数 → 本用例红。
    """
    from app.services import llm_settings_service

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    class _Resolution:
        client = object()
        model = "deepseek-test"

    async def _resolve(_db, _user, **_kwargs):
        return _Resolution()

    def _boom(_client, _model):
        async def _fn(_case, _items):
            raise rubric_service.RubricJudgeUnavailableError("judge down")

        return _fn

    monkeypatch.setattr(llm_settings_service, "resolve_for_user", _resolve)
    monkeypatch.setattr(rubric_service, "make_llm_verdict_fn", _boom)

    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={
                "mode": "judge",
                "cases": [{"case_id": "c1", "answer": "答案"}],
            },
        )
        assert run.status_code == 502, run.text
        assert run.json()["error_code"] == rubric_service.ERROR_CODE_JUDGE_UNAVAILABLE
        run_id = run.json()["detail"]["run_id"]

        detail = await ac.get(f"/api/v1/eval/runs/{run_id}")
        assert detail.status_code == 200, detail.text
        failed = detail.json()["data"]
        assert failed["status"] == "failed"
        assert failed["error_code"] == rubric_service.ERROR_CODE_JUDGE_UNAVAILABLE
        assert failed["aggregate"] is None
        assert failed["results"] is None


@pytest.mark.asyncio
async def test_judge_success_uses_injected_verdicts(factory, monkeypatch):
    """judge 模式成功路径：评委判定被用于评分，评委模型名被冻结进运行。"""
    from app.services import llm_settings_service

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )

    class _Resolution:
        client = object()
        model = "deepseek-test"

    async def _resolve(_db, _user, **_kwargs):
        return _Resolution()

    captured = {}

    def _fake_make(client, model):
        captured["model"] = model

        async def _fn(case, items):
            return {item["key"]: "yes" for item in items}

        return _fn

    monkeypatch.setattr(llm_settings_service, "resolve_for_user", _resolve)
    monkeypatch.setattr(rubric_service, "make_llm_verdict_fn", _fake_make)

    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "judge", "cases": [{"case_id": "c1", "answer": "答案"}]},
        )
        assert run.status_code == 200, run.text
        data = run.json()["data"]
        assert data["judge_model"] == "deepseek-test"
        assert data["aggregate"]["score"] == 1.0
        assert captured["model"] == "deepseek-test"


@pytest.mark.asyncio
async def test_run_invalid_payload_is_422(factory, monkeypatch):
    """运行载荷非法：空 cases / 重复 case_id / 未知判定值 / 未知模式 → 422。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        endpoint = f"/api/v1/eval/rubrics/{created['id']}/runs"
        for payload in (
            {"mode": "fake", "cases": []},
            {
                "mode": "fake",
                "cases": [FAKE_CASES[0], {**FAKE_CASES[0]}],  # 重复 case_id
            },
            {
                "mode": "fake",
                "cases": [
                    {
                        "case_id": "c1",
                        "answer": "a",
                        "fake_verdicts": {"grounded": "maybe"},
                    }
                ],
            },
            {"mode": "nonsense", "cases": FAKE_CASES},
            {"mode": "fake", "cases": [{"case_id": "c1", "answer": "   "}]},
        ):
            r = await ac.post(endpoint, json=payload)
            assert r.status_code == 422, (payload, r.text)

        # 服务层第二道校验：fake 模式缺 fake_verdicts（schema 合法）→ 400。
        missing = await ac.post(
            endpoint,
            json={"mode": "fake", "cases": [{"case_id": "c1", "answer": "a"}]},
        )
        assert missing.status_code in (400, 422), missing.text


@pytest.mark.asyncio
async def test_archived_rubric_run_is_409(factory, monkeypatch):
    """归档评分表不可发起新运行（409），历史运行仍可读。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        run_id = run.json()["data"]["id"]
        await ac.post(f"/api/v1/eval/rubrics/{created['id']}/archive")
        blocked = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error_code"] == "EVAL_RUBRIC_ARCHIVED"
        detail = await ac.get(f"/api/v1/eval/runs/{run_id}")
        assert detail.status_code == 200


# ── RBAC / scope 矩阵 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rbac_requires_search_knowledge_permission(factory, monkeypatch):
    """缺 SEARCH_KNOWLEDGE_BASE → 读/写端点都 403（非超管）。

    变异点：删掉 permission_required 依赖 → 本用例红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ),
    )
    async with _Ctx(_user(superuser=False), factory) as ac:
        assert (await ac.get("/api/v1/eval/rubrics")).status_code == 403
        assert (
            await ac.post("/api/v1/eval/rubrics", json={"name": "x", "items": ITEMS})
        ).status_code == 403


@pytest.mark.asyncio
async def test_api_key_scope_matrix(factory, monkeypatch):
    """scope 矩阵：只读 key 可读不可写；写 key 可建可跑；缺 scope → 403。

    变异点：运行端点改成 knowledge:read（只读 key 也能烧 LLM 成本）→ 红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    async with _Ctx(_user(scopes=["knowledge:write"]), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        assert (await ac.get("/api/v1/eval/rubrics")).status_code == 403
    async with _Ctx(_user(scopes=["knowledge:read"]), factory) as ac:
        assert (await ac.get("/api/v1/eval/rubrics")).status_code == 200
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert run.status_code == 403
        assert (
            await ac.post("/api/v1/eval/rubrics", json={"name": "y", "items": ITEMS})
        ).status_code == 403
    async with _Ctx(_user(scopes=["chat:read"]), factory) as ac:
        assert (await ac.get("/api/v1/eval/rubrics")).status_code == 403
    async with _Ctx(_user(scopes=["knowledge:write"]), factory) as ac:
        run = await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
        assert run.status_code == 200, run.text


# ── 审计 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_detail_contains_metadata_not_content(factory, monkeypatch):
    """审计明细只含元数据：问题/答案/判定正文绝不落审计。

    口径：``audit_service`` 用独立会话写审计，与测试 SQLite 库不同源；这里把
    ``log_activity`` 换成记录调用的替身，只断言「传了什么」（与
    ``test_review_template_api`` 的审计用例同法）。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.SEARCH_KNOWLEDGE_BASE),
    )
    audit_mock = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.eval_rubrics.audit_service.log_activity", audit_mock)
    async with _Ctx(_user(), factory) as ac:
        created = await _create_rubric(ac, items=ITEMS)
        await ac.post(
            f"/api/v1/eval/rubrics/{created['id']}/runs",
            json={"mode": "fake", "cases": FAKE_CASES},
        )
    calls = audit_mock.await_args_list
    actions = [call.kwargs["action"] for call in calls]
    assert actions == ["create_eval_rubric", "run_eval_rubric"]
    for call in calls:
        detail_text = call.kwargs["detail"]
        assert "问题" not in detail_text, detail_text
        assert "答案" not in detail_text, detail_text
        assert "verdict" not in detail_text, detail_text
        assert "根据制度" not in detail_text, detail_text
        payload = json.loads(detail_text)
        assert payload["organization_id"] == ORG
        assert payload["user_id"] == USER_ID
