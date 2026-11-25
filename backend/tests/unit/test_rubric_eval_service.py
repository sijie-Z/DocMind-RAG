"""Rubric 评测服务：不可变版本 / 组织隔离 / 加权评分 / 严格度重映射 / 校准 / 失败语义。

每个用例的 docstring 写出「改了什么会变红」：

1. 版本不可变：新增版本后旧行内容与哈希不变（create_version 改成 update → 红）；
2. 组织隔离：跨组织读取/运行 404（get_rubric 去掉 org 校验 → 红）；
3. 加权得分：Σ(weight×credit)/Σweight（权重被当成等权 → 红）；
4. 严格度重映射：unsure 按 neutral=0.5 / strict=0 / lenient=1 折算
   （remap 忽略 strictness → 红）；
5. 校准数学：一致率 + 逐问题一致率 + 混淆矩阵；无金标 → calibration=None
   （缺失金标被当成 100% → 红）；
6. 评委失败：不可用/契约违规 → status=failed + 机器码、无任何分数，
   绝不静默给 0 分（失败被吞成 0 分 → 红）；
7. fake 模式确定性：同输入两次运行得分完全一致；缺 fake_verdicts → 400。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.eval_rubric import EvalRubricVersion
from app.models.organization import Organization
from app.models.user import User
from app.services import rubric_eval_service as svc

ORG = 1
OTHER_ORG = 2
USER_ID = 1


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rubric-eval.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=0))
        db.add(
            User(
                id=USER_ID,
                username="rubric-user",
                email="rubric@example.com",
                hashed_password="x",
                is_active=True,
                organization_id=ORG,
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


def _items() -> list[dict]:
    return [
        {
            "key": "grounded",
            "question": "回答是否与资料一致？",
            "weight": 3,
            "strictness": "neutral",
        },
        {"key": "cites", "question": "是否给出可核对依据？", "weight": 2, "strictness": "neutral"},
        {"key": "no_fab", "question": "是否没有编造数字？", "weight": 1, "strictness": "strict"},
    ]


async def _rubric_with_version(factory, *, org_id: int = ORG, items=None, name="回答质量"):
    async with factory() as db:
        rubric = await svc.create_rubric(db, organization_id=org_id, created_by=USER_ID, name=name)
        version = await svc.create_version(
            db,
            rubric=rubric,
            items=items or _items(),
            created_by=USER_ID,
        )
        return rubric, version


def _fake_cases() -> list[dict]:
    return [
        {
            "case_id": "c1",
            "name": "好答案",
            "answer": "根据《采购制度》第3条，付款期限为30天。",
            "fake_verdicts": {"grounded": "yes", "cites": "yes", "no_fab": "yes"},
        },
        {
            "case_id": "c2",
            "name": "差答案",
            "answer": "大概三十天吧。",
            "fake_verdicts": {"grounded": "no", "cites": "no", "no_fab": "unsure"},
        },
    ]


# ── 1. 版本不可变 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_versions_are_immutable_and_latest_advances(factory):
    """新增版本只追加行：v1 的内容与哈希在其后新增 v2 时保持不变。

    变异点：create_version 改成原地更新旧行 → v1 哈希断言红；
    latest_version 不再递增 → 详情断言红。
    """
    rubric, v1 = await _rubric_with_version(factory)
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        v2 = await svc.create_version(
            db,
            rubric=loaded,
            items=[{**_items()[0], "weight": 5}],
            created_by=USER_ID,
            change_note="提高 grounded 权重",
        )
        assert v2.version_no == 2
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        assert loaded.latest_version == 2
        row1 = await db.get(EvalRubricVersion, v1.id)
        assert row1.payload_sha256 == v1.payload_sha256
        assert row1.payload_json == v1.payload_json
        row2 = await db.get(EvalRubricVersion, v2.id)
        assert row2.payload_json[0]["weight"] == 5


@pytest.mark.asyncio
async def test_duplicate_rubric_name_conflicts(factory):
    """同组织重名 409；另一组织同名可建（唯一约束按组织）。"""
    await _rubric_with_version(factory, name="重名")
    async with factory() as db:
        with pytest.raises(ConflictError) as caught:
            await svc.create_rubric(db, organization_id=ORG, created_by=USER_ID, name="重名")
        assert caught.value.error_code == "EVAL_RUBRIC_NAME_CONFLICT"
        other = await svc.create_rubric(
            db, organization_id=OTHER_ORG, created_by=USER_ID, name="重名"
        )
        assert other.organization_id == OTHER_ORG


# ── 2. 组织隔离 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_org_rubric_is_not_found(factory):
    """跨组织读取/版本读取一律 404，不回显存在性。"""
    rubric, _ = await _rubric_with_version(factory, org_id=OTHER_ORG)
    async with factory() as db:
        with pytest.raises(NotFoundError):
            await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        own, _ = await _rubric_with_version(factory, org_id=ORG)
        with pytest.raises(NotFoundError):
            await svc.get_version(db, organization_id=OTHER_ORG, rubric=own, version_no=1)


# ── 3/4. 加权得分 + 严格度重映射 ────────────────────────────────────────────


def test_weighted_score_math():
    """加权得分 = Σ(weight×credit)/Σweight；等权会得到不同数字。"""
    items = [
        {"key": "a", "question": "q", "weight": 3, "strictness": "neutral"},
        {"key": "b", "question": "q", "weight": 1, "strictness": "neutral"},
    ]
    scored = svc.score_verdicts(items, {"a": "yes", "b": "no"})
    assert scored["weight_total"] == 4
    assert scored["score"] == 0.75  # (3*1 + 1*0) / 4
    # 等权口径会得到 0.5 —— 证明权重真的参与计算。
    assert scored["score"] != 0.5


def test_strictness_remaps_unsure():
    """unsure 的折算由 strictness 决定：neutral=0.5 / strict=0 / lenient=1。"""
    items = [
        {"key": "a", "question": "q", "weight": 1, "strictness": "neutral"},
        {"key": "b", "question": "q", "weight": 1, "strictness": "strict"},
        {"key": "c", "question": "q", "weight": 1, "strictness": "lenient"},
    ]
    scored = svc.score_verdicts(items, {"a": "unsure", "b": "unsure", "c": "unsure"})
    assert scored["score"] == 0.5  # (0.5 + 0 + 1) / 3
    by_key = {entry["key"]: entry["credit"] for entry in scored["items"]}
    assert by_key == {"a": 0.5, "b": 0.0, "c": 1.0}


def test_yes_no_ignore_strictness():
    """strictness 只影响 unsure：yes/no 在任何严格度下恒为 1/0。"""
    for strictness in ("neutral", "strict", "lenient"):
        assert svc.verdict_credit("yes", strictness) == 1.0
        assert svc.verdict_credit("no", strictness) == 0.0


# ── 5. 校准数学 ─────────────────────────────────────────────────────────────


def test_calibration_agreement_and_confusion():
    """一致率 = 相同判定对数 / 可比对总数；逐问题一致率与混淆矩阵同口径。"""
    items = [
        {"key": "a", "question": "q", "weight": 1, "strictness": "neutral"},
        {"key": "b", "question": "q", "weight": 1, "strictness": "neutral"},
    ]
    cases = [
        {"case_id": "c1", "gold_verdicts": {"a": "yes", "b": "no"}},
        {"case_id": "c2", "gold_verdicts": {"a": "yes"}},  # b 缺金标：不参与
        {"case_id": "c3"},  # 无金标：整条跳过
    ]
    results = [
        {
            "case_id": "c1",
            "items": [{"key": "a", "verdict": "yes"}, {"key": "b", "verdict": "yes"}],
        },
        {"case_id": "c2", "items": [{"key": "a", "verdict": "no"}, {"key": "b", "verdict": "yes"}]},
        {
            "case_id": "c3",
            "items": [{"key": "a", "verdict": "yes"}, {"key": "b", "verdict": "yes"}],
        },
    ]
    report = svc.calibrate(cases, items, results)
    assert report is not None
    assert report["pairs_compared"] == 3  # c1: a,b + c2: a
    assert report["pairs_agreed"] == 1  # 只有 c1.a 一致
    assert report["agreement_rate"] == round(1 / 3, 4)
    assert report["confusion"]["yes"]["yes"] == 1
    assert report["confusion"]["yes"]["no"] == 1
    assert report["confusion"]["no"]["yes"] == 1
    question_a = next(q for q in report["questions"] if q["key"] == "a")
    assert question_a == {"key": "a", "compared": 2, "agreed": 1, "agreement_rate": 0.5}
    question_b = next(q for q in report["questions"] if q["key"] == "b")
    assert question_b["compared"] == 1 and question_b["agreed"] == 0


def test_calibration_is_null_without_gold():
    """没有任何金标时 calibration 显式为 None —— 绝不展示成「100% 准确」。"""
    items = [{"key": "a", "question": "q", "weight": 1, "strictness": "neutral"}]
    cases = [{"case_id": "c1"}, {"case_id": "c2", "gold_verdicts": {}}]
    results = [
        {"case_id": "c1", "score": 1.0, "items": [{"key": "a", "verdict": "yes", "credit": 1.0}]},
        {"case_id": "c2", "score": 1.0, "items": [{"key": "a", "verdict": "yes", "credit": 1.0}]},
    ]
    assert svc.calibrate(cases, items, results) is None
    aggregate = svc.aggregate_results(items, cases, results)
    assert aggregate["calibration"] is None


# ── 6. 评委失败语义 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_unavailable_fails_run_without_scores(factory):
    """评委调用失败 → status=failed + JUDGE_UNAVAILABLE，aggregate/results 为空。

    变异点：把 RubricJudgeUnavailableError 吞成 0 分结果 → 本用例红。
    """
    rubric, _ = await _rubric_with_version(factory)

    async def _boom(case, items):
        raise svc.RubricJudgeUnavailableError("judge down")

    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        run = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_JUDGE,
            raw_cases=[{"case_id": "c1", "answer": "答案"}],
            verdict_fn=_boom,
            judge_model="deepseek-test",
        )
        assert run.status == "failed"
        assert run.error_code == svc.ERROR_CODE_JUDGE_UNAVAILABLE
        assert run.aggregate is None
        assert run.results is None
        assert run.case_count == 1
        assert run.completed_at is not None


@pytest.mark.asyncio
async def test_judge_contract_violation_fails_run(factory):
    """评委输出缺键/非法值 → JUDGE_CONTRACT_VIOLATION，绝不补默认值。"""
    rubric, _ = await _rubric_with_version(factory)

    async def _missing_key(case, items):
        return {"grounded": "yes"}  # 缺 cites / no_fab

    async def _bad_value(case, items):
        return {"grounded": "yes", "cites": "yes", "no_fab": "maybe"}

    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        for fn in (_missing_key, _bad_value):
            run = await svc.execute_run(
                db,
                organization_id=ORG,
                rubric=loaded,
                version_no=None,
                created_by=USER_ID,
                mode=svc.RUN_MODE_JUDGE,
                raw_cases=[{"case_id": "c1", "answer": "答案"}],
                verdict_fn=fn,
                judge_model="deepseek-test",
            )
            assert run.status == "failed"
            assert run.error_code == svc.ERROR_CODE_JUDGE_CONTRACT
            assert run.aggregate is None


@pytest.mark.asyncio
async def test_judge_run_exceeding_budget_fails_explicitly(factory, monkeypatch):
    """超过服务端墙钟预算 → 显式失败码且无分数，而不是被代理切断成幽灵运行。

    变异点：去掉 deadline 检查 → 本用例变红（会跑完全部用例并按完成落库）。
    """
    rubric, _ = await _rubric_with_version(factory)
    monkeypatch.setattr(svc, "MAX_JUDGE_RUN_SECONDS", 0.0)

    async def _slow(case, items):
        return {"grounded": "yes", "cites": "yes", "no_fab": "yes"}

    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        run = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_JUDGE,
            raw_cases=[{"case_id": "c1", "answer": "答案"}],
            verdict_fn=_slow,
            judge_model="deepseek-test",
        )
        assert run.status == "failed"
        assert run.error_code == "JUDGE_RUN_BUDGET_EXCEEDED"
        assert run.aggregate is None


@pytest.mark.asyncio
async def test_judge_partial_failure_fails_whole_run(factory):
    """第二条用例评委失败 → 整次运行失败，不给「部分分数」。

    变异点：失败用例被跳过、其余照常出分 → 本用例红（部分分数会掩盖故障）。
    """
    rubric, _ = await _rubric_with_version(factory)
    calls = {"count": 0}

    async def _flaky(case, items):
        calls["count"] += 1
        if calls["count"] == 2:
            raise svc.RubricJudgeUnavailableError("boom")
        return {"grounded": "yes", "cites": "yes", "no_fab": "yes"}

    raw_cases = [
        {"case_id": "c1", "answer": "a1"},
        {"case_id": "c2", "answer": "a2"},
    ]
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        run = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_JUDGE,
            raw_cases=raw_cases,
            verdict_fn=_flaky,
            judge_model="deepseek-test",
        )
        assert run.status == "failed"
        assert run.results is None


# ── 7. fake 模式确定性 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_mode_is_deterministic_and_persists_snapshot(factory):
    """fake 模式两次运行得分与逐条结果一致；数据集哈希稳定；版本号被冻结。"""
    rubric, version = await _rubric_with_version(factory)
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        first = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_FAKE,
            raw_cases=_fake_cases(),
        )
        second = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_FAKE,
            raw_cases=_fake_cases(),
        )
    assert first.status == "completed"
    assert first.rubric_version_no == version.version_no
    assert first.judge_model == svc.FAKE_JUDGE_MODEL
    assert first.dataset_hash == second.dataset_hash
    assert first.aggregate == second.aggregate
    # c1 = 1.0；c2 = (3*0 + 2*0 + 1*0) / 6 = 0.0 → 宏平均 0.5
    assert first.aggregate["score"] == 0.5
    assert first.aggregate["cases_scored"] == 2
    by_key = {row["key"]: row for row in first.aggregate["questions"]}
    assert by_key["grounded"]["pass_rate"] == 0.5
    assert by_key["no_fab"]["verdict_counts"] == {"yes": 1, "no": 0, "unsure": 1}
    assert first.aggregate["calibration"] is None


@pytest.mark.asyncio
async def test_fake_mode_requires_fake_verdicts(factory):
    """fake 模式缺 fake_verdicts / 覆盖不全 → 400，绝不补默认判定。"""
    rubric, _ = await _rubric_with_version(factory)
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        with pytest.raises(ValidationError):
            await svc.execute_run(
                db,
                organization_id=ORG,
                rubric=loaded,
                version_no=None,
                created_by=USER_ID,
                mode=svc.RUN_MODE_FAKE,
                raw_cases=[{"case_id": "c1", "answer": "a"}],
            )
        with pytest.raises(ValidationError):
            await svc.execute_run(
                db,
                organization_id=ORG,
                rubric=loaded,
                version_no=None,
                created_by=USER_ID,
                mode=svc.RUN_MODE_FAKE,
                raw_cases=[
                    {
                        "case_id": "c1",
                        "answer": "a",
                        "fake_verdicts": {"grounded": "yes"},  # 缺两项
                    }
                ],
            )


@pytest.mark.asyncio
async def test_run_with_gold_reports_calibration(factory):
    """带金标的 fake 运行：aggregate.calibration 有值，一致率按判定对计算。"""
    rubric, _ = await _rubric_with_version(factory)
    cases = _fake_cases()
    cases[0]["gold_verdicts"] = {"grounded": "yes", "cites": "yes", "no_fab": "yes"}
    cases[1]["gold_verdicts"] = {"grounded": "no", "cites": "yes", "no_fab": "unsure"}
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        run = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=None,
            created_by=USER_ID,
            mode=svc.RUN_MODE_FAKE,
            raw_cases=cases,
        )
    calibration = run.aggregate["calibration"]
    assert calibration is not None
    assert calibration["pairs_compared"] == 6
    # c2: cites judge=no vs gold=yes 不一致；其余 5 对一致。
    assert calibration["pairs_agreed"] == 5
    assert calibration["agreement_rate"] == round(5 / 6, 4)


# ── 其他：归档 / 损坏版本 / 版本号选择 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_archived_rubric_rejects_versions_and_runs(factory):
    """归档后：不可新增版本、不可发起运行（409），行不删除。"""
    rubric, _ = await _rubric_with_version(factory)
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        await svc.archive_rubric(db, rubric=loaded, user_id=USER_ID)
        with pytest.raises(ConflictError) as caught:
            await svc.create_version(db, rubric=loaded, items=_items(), created_by=USER_ID)
        assert caught.value.error_code == "EVAL_RUBRIC_ARCHIVED"
        with pytest.raises(ConflictError):
            await svc.execute_run(
                db,
                organization_id=ORG,
                rubric=loaded,
                version_no=None,
                created_by=USER_ID,
                mode=svc.RUN_MODE_FAKE,
                raw_cases=_fake_cases(),
            )
        with pytest.raises(ConflictError):
            await svc.archive_rubric(db, rubric=loaded, user_id=USER_ID)
        assert await db.get(svc.EvalRubric, rubric.id) is not None


@pytest.mark.asyncio
async def test_corrupted_version_is_rejected_for_run(factory):
    """版本内容哈希不符 = 损坏；运行拒绝使用（409），绝不把不可信评分项当依据。"""
    rubric, version = await _rubric_with_version(factory)
    async with factory() as db:
        row = await db.get(EvalRubricVersion, version.id)
        row.payload_json = [{**row.payload_json[0], "question": "被篡改"}]
        await db.commit()
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        with pytest.raises(ConflictError) as caught:
            await svc.execute_run(
                db,
                organization_id=ORG,
                rubric=loaded,
                version_no=None,
                created_by=USER_ID,
                mode=svc.RUN_MODE_FAKE,
                raw_cases=_fake_cases(),
            )
        assert caught.value.error_code == "EVAL_RUBRIC_VERSION_CORRUPT"
        detail = await svc.get_version(
            db, organization_id=ORG, rubric=loaded, version_no=version.version_no
        )
        assert svc.version_to_dict(detail)["intact"] is False


@pytest.mark.asyncio
async def test_run_pins_requested_version_not_latest(factory):
    """显式 rubric_version_no 生效：运行记录冻结的是被选版本，不是最新版。"""
    rubric, v1 = await _rubric_with_version(factory)
    async with factory() as db:
        loaded = await svc.get_rubric(db, organization_id=ORG, rubric_id=rubric.id)
        await svc.create_version(db, rubric=loaded, items=_items(), created_by=USER_ID)
        run = await svc.execute_run(
            db,
            organization_id=ORG,
            rubric=loaded,
            version_no=1,
            created_by=USER_ID,
            mode=svc.RUN_MODE_FAKE,
            raw_cases=_fake_cases(),
        )
    assert run.rubric_version_no == 1


def test_normalize_items_rejects_invalid_shapes():
    """非法评分项（空列表/非法 key/重复 key/权重越界/未知严格度）一律 400。"""
    bad_payloads = [
        [],
        [{"key": "BadKey", "question": "q"}],
        [{"key": "a", "question": ""}],
        [{"key": "a", "question": "q"}, {"key": "a", "question": "q2"}],
        [{"key": "a", "question": "q", "weight": 0}],
        [{"key": "a", "question": "q", "weight": 11}],
        [{"key": "a", "question": "q", "strictness": "sloppy"}],
    ]
    for payload in bad_payloads:
        with pytest.raises(ValidationError):
            svc.normalize_rubric_items(payload)


def test_diff_versions_reports_field_changes():
    """diff 按 key 对齐，逐字段给出两侧值；相同内容 identical=True。"""

    class _Row:
        def __init__(self, items, version_no):
            self.id = f"v{version_no}"
            self.version_no = version_no
            self.payload_json = items
            self.payload_sha256 = "x" * 64
            self.item_count = len(items)

    base = _Row([{"key": "a", "question": "q1", "weight": 1, "strictness": "neutral"}], 1)
    target = _Row(
        [
            {"key": "a", "question": "q2", "weight": 2, "strictness": "neutral"},
            {"key": "b", "question": "qb", "weight": 1, "strictness": "strict"},
        ],
        2,
    )
    diff = svc.diff_versions(base, target)
    assert diff["identical"] is False
    assert diff["summary"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 0}
    changed = diff["changed"][0]
    assert changed["key"] == "a"
    assert changed["fields"]["question"] == {"base": "q1", "target": "q2"}
    assert changed["fields"]["weight"] == {"base": 1, "target": 2}
    assert svc.diff_versions(base, base)["identical"] is True


def test_bootstrap_default_items_are_valid():
    """服务端默认评分项必须能通过规范化（防止默认值自己违反约束）。"""
    normalized = svc.normalize_rubric_items(svc.default_items())
    assert len(normalized) == len(svc.DEFAULT_RUBRIC_ITEMS)
    assert all(item["weight"] >= 1 for item in normalized)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return _FakeResponse(self._contents.pop(0))


class _FakeClient:
    def __init__(self, contents):
        self.chat = type("C", (), {"completions": _FakeCompletions(contents)})()


@pytest.mark.asyncio
async def test_judge_empty_content_is_retried_once_then_succeeds(monkeypatch):
    """推理模型偶发空内容：重试一次并成功，而不是把整次运行打成契约违规。

    变异点：去掉空内容重试 → 本用例变红（第一次空响应就抛契约错误）。
    """
    calls = {"n": 0}

    async def fake_completion(client, *, read_timeout, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse("")
        return _FakeResponse('{"verdicts": {"grounded": "yes"}}')

    monkeypatch.setattr(svc, "create_chat_completion", fake_completion)
    verdict_fn = svc.make_llm_verdict_fn(_FakeClient([]), "m")
    result = await verdict_fn({"answer": "答案"}, [{"key": "grounded", "question": "是否有据"}])
    assert result == {"grounded": "yes"}
    assert calls["n"] == 2, "空内容必须重试一次"


@pytest.mark.asyncio
async def test_judge_two_empty_contents_fail_explicitly(monkeypatch):
    """连续两次空内容 → 显式契约错误，绝不当成 0 分或默认值。"""

    async def fake_completion(client, *, read_timeout, **kwargs):
        return _FakeResponse("")

    monkeypatch.setattr(svc, "create_chat_completion", fake_completion)
    verdict_fn = svc.make_llm_verdict_fn(_FakeClient([]), "m")
    with pytest.raises(svc.RubricJudgeContractError):
        await verdict_fn({"answer": "答案"}, [{"key": "grounded", "question": "是否有据"}])
