"""竞品对标 P1：合同审查规则快照的判别性测试。

覆盖：建案写入快照、规则配置变更后旧案件仍用旧快照、旧数据回落、
payload 与 sha256 一致（篡改即回落）、跨组织隔离。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.models.review import ReviewCase, ReviewRuleSet
from app.services import review_case_service as case_service
from app.services import review_rule_snapshot
from app.services.review_checklist import ChecklistItem, load_checklist
from app.services.review_rule_snapshot import (
    _digest,
    attach_snapshot,
    build_snapshot_payload,
    case_rule_metadata,
    load_snapshot,
    parse_snapshot,
    resolve_case_rules,
)
from tests.unit.test_review_case_service import ORG, USER_ID, _create_case, _seed_ready_case

EXTRA_KEY = "confidentiality"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rule-snapshot.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _checklist_env(*, keyword: str) -> str:
    return json.dumps([{"key": EXTRA_KEY, "keywords": [keyword], "label": "保密"}])


@pytest.mark.asyncio
async def test_create_case_persists_rule_snapshot(factory, monkeypatch):
    """建案时从 loader 派生快照并写入案件 FK，payload 与 sha256 一致。"""
    monkeypatch.setenv("REVIEW_CHECKLIST_JSON", _checklist_env(keyword="保密"))
    await _seed_ready_case(factory)
    case = await _create_case(factory)

    assert case.rule_snapshot_id is not None
    async with factory() as db:
        row = await db.get(ReviewRuleSet, case.rule_snapshot_id)
        assert row is not None
        assert row.organization_id == ORG
        assert row.version == "v1"
        assert (
            row.payload_sha256
            == hashlib.sha256(
                json.dumps(
                    row.payload_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
        )
        assert [rule["key"] for rule in row.payload_json["rules"]] == [
            "payment_term",
            "start_condition",
            "acceptance_invoice",
            "exception",
            EXTRA_KEY,
        ]
        assert row.payload_json["rules"][-1]["keywords"] == ["保密"]
        assert row.published_at is not None

        detail = await case_service.case_detail(db, case)
        assert detail["from_snapshot"] is True
        assert detail["rule_snapshot_id"] == row.id
        assert detail["rule_snapshot_version"] == row.version
        assert detail["rule_snapshot_sha256"] == row.payload_sha256
        assert EXTRA_KEY in detail["triggered_rule_ids"]


@pytest.mark.asyncio
async def test_rule_change_does_not_rewrite_old_case(factory, monkeypatch):
    """规则配置变更后，旧案件解析仍用快照里的旧关键词。"""
    monkeypatch.setenv("REVIEW_CHECKLIST_JSON", _checklist_env(keyword="旧关键词"))
    await _seed_ready_case(factory)
    case = await _create_case(factory)

    monkeypatch.setenv("REVIEW_CHECKLIST_JSON", _checklist_env(keyword="新关键词"))
    async with factory() as db:
        rules = await resolve_case_rules(db, case)
        assert {rule.key: rule.keywords for rule in rules}[EXTRA_KEY] == ("旧关键词",)
        assert {rule.key: rule.keywords for rule in load_checklist()}[EXTRA_KEY] == ("新关键词",)


@pytest.mark.asyncio
async def test_legacy_case_without_snapshot_falls_back(factory, monkeypatch):
    """旧案件 rule_snapshot_id 为 NULL，回落当前 checklist（父版本行为）。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        case.rule_snapshot_id = None
        await db.commit()

        monkeypatch.setenv("REVIEW_CHECKLIST_JSON", _checklist_env(keyword="现在规则"))
        rules = await resolve_case_rules(db, case)
        assert EXTRA_KEY in {rule.key for rule in rules}
        detail = await case_service.case_detail(db, case)
        assert detail["from_snapshot"] is False
        assert detail["rule_snapshot_id"] is None
        assert detail["rule_snapshot_version"] is None
        assert detail["rule_snapshot_sha256"] is None
        assert "payment_term" in detail["triggered_rule_ids"]
        assert EXTRA_KEY not in detail["triggered_rule_ids"], (
            "旧案件的触发规则只能来自它自己的核验项，不得凭当前配置凭空追加"
        )


@pytest.mark.asyncio
async def test_tampered_payload_falls_back(factory, monkeypatch):
    """payload 与 sha256 不一致时拒绝快照，回落当前 checklist。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        row = await db.get(ReviewRuleSet, case.rule_snapshot_id)
        original = row.payload_json["rules"][0]["keywords"]
        row.payload_json["rules"][0]["keywords"] = ["被篡改"]
        await db.commit()
        assert _digest(row.payload_json) != row.payload_sha256

        rules = await resolve_case_rules(db, case)
        assert [rule.key for rule in rules] == [
            "payment_term",
            "start_condition",
            "acceptance_invoice",
            "exception",
        ]
        metadata = await case_rule_metadata(db, case, [])
        assert metadata["from_snapshot"] is False
        assert original  # 原始关键词不再被快照采用


@pytest.mark.asyncio
async def test_snapshot_is_tenant_isolated(factory, monkeypatch):
    """跨组织引用同一快照 ID 视为无效，不得读取他组织规则。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    foreign = ReviewCase(
        id=str(uuid.uuid4()),
        organization_id=ORG + 1,
        title="外组织案件",
        policy_document_id="p",
        base_document_id="b",
        revised_document_id="r",
        checklist_version="v1",
        status="ready",
        rule_snapshot_id=case.rule_snapshot_id,
        created_by=USER_ID,
        lock_version=1,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    async with factory() as db:
        db.add(foreign)
        await db.commit()
        assert await load_snapshot(db, foreign) is None
        metadata = await case_rule_metadata(db, foreign, [])
        assert metadata["from_snapshot"] is False
        assert metadata["rule_snapshot_id"] is None
        assert metadata["triggered_rule_ids"] == []


@pytest.mark.asyncio
async def test_snapshot_reused_for_identical_rules(factory, monkeypatch):
    """相同组织相同规则复用一行快照，避免重复行。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    first = await _create_case(factory, title="案件一")
    second = await _create_case(factory, title="案件二")
    assert first.rule_snapshot_id == second.rule_snapshot_id
    async with factory() as db:
        rows = (await db.execute(select(ReviewRuleSet))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_tampered_row_organization_falls_back(factory, monkeypatch):
    """只改行级组织列（payload 内组织仍正确）也视为篡改，必须回落。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        row = await db.get(ReviewRuleSet, case.rule_snapshot_id)
        # 行级列改成外组织，但 payload 未动（sha256 仍然自洽）。
        row.organization_id = ORG + 1
        await db.commit()
        assert _digest(row.payload_json) == row.payload_sha256
        assert parse_snapshot(row, organization_id=ORG) is None

        assert await load_snapshot(db, case) is None
        metadata = await case_rule_metadata(db, case, [])
        assert metadata["from_snapshot"] is False


@pytest.mark.asyncio
async def test_payload_organization_mismatch_falls_back(factory, monkeypatch):
    """payload 内冻结的组织与调用方不符时回落（交叉校验 payload）。"""
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        row = await db.get(ReviewRuleSet, case.rule_snapshot_id)
        # 连同哈希一起改：行级组织仍等于 ORG，只有 payload 组织不一致。
        tampered = dict(row.payload_json)
        tampered["organization_id"] = ORG + 1
        row.payload_json = tampered
        row.payload_sha256 = _digest(tampered)
        await db.commit()

        assert parse_snapshot(row, organization_id=ORG) is None
        assert await load_snapshot(db, case) is None
        rules = await resolve_case_rules(db, case)
        assert {rule.key for rule in rules} == {
            "payment_term",
            "start_condition",
            "acceptance_invoice",
            "exception",
        }


@pytest.mark.asyncio
async def test_concurrent_first_snapshot_insert_reuses_winner(factory, monkeypatch):
    """并发首建：INSERT 撞唯一约束后回查复用对手行，而不是把竞态变成 409。

    构造方式：直接构造一个案件对象（不落库），让第一次 `_find_by_digest`
    返回 None（并发窗口内双方都没看到对手行），随后插入会撞
    (organization_id, payload_sha256) 唯一约束。
    """
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    checklist = load_checklist()
    payload = build_snapshot_payload(checklist, ORG)
    digest = _digest(payload)
    winner_id = str(uuid.uuid4())

    async with factory() as db:
        # 对手行：同 (组织, 哈希) 已提交（另一个建案事务的产物）。
        db.add(
            ReviewRuleSet(
                id=winner_id,
                organization_id=ORG,
                version="v1",
                payload_json=payload,
                payload_sha256=digest,
                published_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()

        case = ReviewCase(
            id=str(uuid.uuid4()),
            organization_id=ORG,
            title="并发建案",
            policy_document_id="doc-p",
            base_document_id="doc-b",
            revised_document_id="doc-r",
            checklist_version="v1",
            status="ready",
            created_by=USER_ID,
            lock_version=1,
        )
        db.add(case)

        real_find = review_rule_snapshot._find_by_digest
        calls = {"n": 0}

        async def first_miss_then_real(session, organization_id, digest):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # 模拟并发窗口：首次 SELECT 未命中
            return await real_find(session, organization_id, digest)

        monkeypatch.setattr(review_rule_snapshot, "_find_by_digest", first_miss_then_real)
        row = await attach_snapshot(db, case, checklist)
        await db.commit()

        assert row.id == winner_id, "必须复用已存在的对手行，而不是再插一行"
        assert case.rule_snapshot_id == winner_id
        rows = (await db.execute(select(ReviewRuleSet))).scalars().all()
        assert len(rows) == 1, "竞态后不得留下重复快照行"


def test_rule_snapshot_helper_rejects_malformed_rules():
    """损坏的快照载荷不抛异常，调用方按缺快照处理。"""
    from app.services.review_rule_snapshot import parse_snapshot

    row = ReviewRuleSet(
        id=str(uuid.uuid4()),
        organization_id=ORG,
        version="v1",
        payload_json={"schema_version": 1, "rules": [{"key": "x", "keywords": "not-a-list"}]},
        payload_sha256="x" * 64,
    )
    assert parse_snapshot(row, organization_id=ORG) is None
    assert parse_snapshot(None, organization_id=ORG) is None
    assert isinstance(load_checklist()[0], ChecklistItem)
