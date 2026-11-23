"""批次五：合同审查模板库的判别性测试（服务层，真实 SQLite）。

覆盖与变异说明（每个用例 docstring 都写出「改了什么会变红」）：
1. 版本不可变 + 建案固化快照：模板新版本/归档后，历史案件分析仍用旧规则；
2. 版本 diff 正确性：关键词集合语义（顺序不算差异、增删算）、增删项、label；
3. 组织隔离与内容校验：跨组织 404、归档模板拒建案、被篡改版本拒用；
4. 上限：项数/关键词数/长度/版本数/单组织模板数；内置项不可删改；
5. 归档策略：不物理删除，归档后只读。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.review import ReviewCase, ReviewRuleSet, ReviewTemplate, ReviewTemplateVersion
from app.services import review_template_service as svc
from app.services.review_checklist import DEFAULT_ITEM_KEYWORDS
from tests.unit.test_review_case_service import (
    ORG,
    USER_ID,
    _create_case,
    _items,
    _seed_ready_case,
)

OTHER_ORG = ORG + 1


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-templates.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _builtin_items() -> list[dict]:
    """模板版本必须原样包含四个内置项（正常客户端应当带上的形状）。"""
    return [
        {"key": key, "keywords": list(keywords), "label": ""}
        for key, keywords in DEFAULT_ITEM_KEYWORDS.items()
    ]


def _items_payload(*, extra_keyword: str = "保密") -> list[dict]:
    """自定义追加项（内置 4 项由服务端自动补齐，不需要在这里重复）。"""
    return [
        {"key": "confidentiality", "keywords": ["保密", extra_keyword], "label": "保密义务"},
        {"key": "termination", "keywords": ["解除", "终止"], "label": "解除权"},
    ]


async def _template_with_version(factory, *, org_id: int = ORG, name: str = "采购模板", **kwargs):
    async with factory() as db:
        template = await svc.create_template(
            db, organization_id=org_id, created_by=USER_ID, name=name
        )
        version = await svc.create_version(
            db,
            template=template,
            items=kwargs.pop("items", _items_payload()),
            created_by=USER_ID,
            **kwargs,
        )
        return template, version


def _builtin_keys() -> list[str]:
    return list(DEFAULT_ITEM_KEYWORDS)


# ── 1. 版本不可变 + 建案固化快照 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_from_template_snapshots_rules_and_survives_new_versions(factory, monkeypatch):
    """模板建案固化规则快照；模板新增版本/归档后旧案件分析与规则不变。

    变异点：create_case 改回 `load_checklist()`（忽略模板）→ 案件 item_key 不含
    confidentiality，本用例红；attach_snapshot 改为读取模板最新版而非传入的
    checklist → 新增 v2 后 resolve_case_rules 会看到 v2 关键词，本用例红。
    """
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    template, version = await _template_with_version(factory)

    case = await _create_case(
        factory, template_id=template.id, template_version_no=version.version_no
    )
    assert case.template_id == template.id
    assert case.template_version_no == version.version_no
    assert case.rule_snapshot_id is not None

    items = await _items(factory, case.id)
    assert [item.item_key for item in items] == [*_builtin_keys(), "confidentiality", "termination"]

    async with factory() as db:
        snapshot = await db.get(ReviewRuleSet, case.rule_snapshot_id)
        assert snapshot.organization_id == ORG
        keys = [rule["key"] for rule in snapshot.payload_json["rules"]]
        assert keys == [*_builtin_keys(), "confidentiality", "termination"]
        custom = {rule["key"]: rule for rule in snapshot.payload_json["rules"]}
        assert custom["confidentiality"]["keywords"] == ["保密"], "重复关键词按首次出现去重"

    # 模板编辑（v2 追加新项）+ 归档：历史案件仍解析到建案时的快照规则。
    async with factory() as db:
        fresh = await svc.get_template(db, organization_id=ORG, template_id=template.id)
        await svc.create_version(
            db,
            template=fresh,
            items=[{"key": "warranty", "keywords": ["质保"], "label": "质保期"}],
            created_by=USER_ID,
            change_note="v2 追加质保项",
        )
        await svc.archive_template(db, template=fresh, user_id=USER_ID)

    async with factory() as db:
        case_row = await db.get(ReviewCase, case.id)
        from app.services.review_rule_snapshot import resolve_case_rules

        rules = await resolve_case_rules(db, case_row)
        by_key = {rule.key: rule.keywords for rule in rules}
        assert "warranty" not in by_key, "历史案件必须仍用建案时快照，看不到 v2 追加项"
        assert by_key["confidentiality"] == ("保密",)

        snapshot_row = await db.get(ReviewRuleSet, case_row.rule_snapshot_id)
        assert "warranty" not in [r["key"] for r in snapshot_row.payload_json["rules"]], (
            "模板新增版本不得改写既有快照行"
        )


@pytest.mark.asyncio
async def test_case_without_template_keeps_process_checklist(factory, monkeypatch):
    """不指定模板时行为与父版本一致（默认 4 项，无模板溯源指针）。

    变异点：create_case 无条件走模板路径 → 本用例红（4 项断言失败）。
    """
    monkeypatch.delenv("REVIEW_CHECKLIST_JSON", raising=False)
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    assert case.template_id is None and case.template_version_no is None
    items = await _items(factory, case.id)
    assert [item.item_key for item in items] == _builtin_keys()


@pytest.mark.asyncio
async def test_default_version_is_latest_and_explicit_missing_version_404(factory):
    """缺省版本取最新版；显式指定不存在的版本 404（不静默回落）。

    变异点：resolve_template_items 在不存在的版本上回落最新版 → 第二个断言红。
    """
    await _seed_ready_case(factory)
    template, _first = await _template_with_version(factory)
    async with factory() as db:
        fresh = await svc.get_template(db, organization_id=ORG, template_id=template.id)
        second = await svc.create_version(
            db,
            template=fresh,
            items=[{"key": "warranty", "keywords": ["质保"], "label": "质保"}],
            created_by=USER_ID,
        )
    case = await _create_case(factory, template_id=template.id)
    assert case.template_version_no == second.version_no
    items = await _items(factory, case.id)
    assert items[-1].item_key == "warranty"

    with pytest.raises(NotFoundError):
        await _create_case(factory, template_id=template.id, template_version_no=999)


@pytest.mark.asyncio
async def test_template_case_snapshot_metadata_in_detail(factory):
    """案件详情同时暴露模板溯源指针与规则快照哈希（两者不混用）。"""
    await _seed_ready_case(factory)
    template, version = await _template_with_version(factory)
    case = await _create_case(factory, template_id=template.id)
    from app.services import review_case_service as case_service

    async with factory() as db:
        detail = await case_service.case_detail(db, case)
    assert detail["template_id"] == template.id
    assert detail["template_version_no"] == version.version_no
    assert detail["from_snapshot"] is True
    assert detail["rule_snapshot_sha256"]


# ── 2. 版本 diff ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_keyword_set_semantics_added_removed_items(factory):
    """diff 正确性：关键词顺序不算差异、增删算；项增删与 label 变化逐字段报告。

    变异点：diff 改成按列表顺序比较关键词 → 第一个 changed 用例红；
    漏掉 removed 分支 → removed 断言红。
    """
    template, _ = await _template_with_version(factory)
    base_items = [
        # 与 target 关键词顺序相反（集合相等）：不应算差异。
        {"key": "confidentiality", "keywords": ["保密", "泄露"], "label": "保密义务"},
        {"key": "termination", "keywords": ["解除"], "label": "解除权"},
        {"key": "audit", "keywords": ["审计"], "label": "审计权"},
    ]
    async with factory() as db:
        fresh = await svc.get_template(db, organization_id=ORG, template_id=template.id)
        base_row = await svc.create_version(
            db, template=fresh, items=base_items, created_by=USER_ID
        )
        target_items = [
            {"key": "confidentiality", "keywords": ["泄露", "保密"], "label": "保密义务"},
            {"key": "termination", "keywords": ["解除", "终止"], "label": "解除权（含终止）"},
            {"key": "payment_clause", "keywords": ["付款期限"], "label": "付款期限"},
        ]
        target_row = await svc.create_version(
            db, template=fresh, items=target_items, created_by=USER_ID
        )
        diff = svc.diff_versions(base_row, target_row)

    assert diff["summary"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}
    assert [entry["key"] for entry in diff["added"]] == ["payment_clause"]
    assert [entry["key"] for entry in diff["removed"]] == ["audit"]
    changed = diff["changed"][0]
    assert changed["key"] == "termination"
    assert changed["fields"]["keywords"]["added"] == ["终止"]
    assert changed["fields"]["keywords"]["removed"] == []
    assert changed["fields"]["label"] == {"base": "解除权", "target": "解除权（含终止）"}
    assert "confidentiality" in diff["unchanged_keys"]
    assert diff["base"]["version_no"] == base_row.version_no
    assert diff["target"]["version_no"] == target_row.version_no


@pytest.mark.asyncio
async def test_diff_identical_versions(factory):
    """同一版本对自身 diff：identical=True 且三类差异全空。"""
    template, version = await _template_with_version(factory)
    async with factory() as db:
        row = await svc.get_template_version(
            db, organization_id=ORG, template=template, version_no=version.version_no
        )
        diff = svc.diff_versions(row, row)
    assert diff["identical"] is True
    # 内置基线不进入 diff，自定义项为 2 个（confidentiality/termination）。
    assert diff["summary"] == {"added": 0, "removed": 0, "changed": 0, "unchanged": 2}


# ── 3. 组织隔离 / 归档 / 内容校验 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_org_template_is_not_found(factory):
    """跨组织读取/建案一律 404（不回显存在性）。

    变异点：get_template 去掉 organization_id 校验 → 本用例红。
    """
    template, version = await _template_with_version(factory, org_id=ORG)
    async with factory() as db:
        with pytest.raises(NotFoundError):
            await svc.get_template(db, organization_id=OTHER_ORG, template_id=template.id)

    # 案件建案复用同一 org_id（docs 来自 ORG）：它属于调用方组织，模板属于
    # 另一组织 → 必须按「模板不存在」拒绝，而不是把外组织规则带进本组织案件。
    await _seed_ready_case(factory)
    async with factory() as db:
        foreign = await svc.create_template(
            db, organization_id=OTHER_ORG, created_by=USER_ID, name="外组织模板"
        )
        await svc.create_version(db, template=foreign, items=_items_payload(), created_by=USER_ID)
    with pytest.raises(NotFoundError):
        await _create_case(factory, template_id=foreign.id)
    # 同 id 的版本也不可跨组织读取
    async with factory() as db:
        with pytest.raises(NotFoundError):
            await svc.get_template_version(
                db,
                organization_id=OTHER_ORG,
                template=template,
                version_no=version.version_no,
            )


@pytest.mark.asyncio
async def test_archived_template_rejects_new_versions_and_cases(factory):
    """归档模板：只读（不可新增版本/改名），且不能再被新案件选择。

    变异点：resolve_template_items 不检查归档状态 → 建案断言红；
    create_version 不检查归档状态 → 409 断言红。
    """
    template, _ = await _template_with_version(factory)
    async with factory() as db:
        fresh = await svc.get_template(db, organization_id=ORG, template_id=template.id)
        archived = await svc.archive_template(db, template=fresh, user_id=USER_ID)
        assert archived.status == svc.TEMPLATE_STATUS_ARCHIVED
        assert archived.archived_at is not None
        with pytest.raises(ConflictError) as excinfo:
            await svc.create_version(
                db,
                template=archived,
                items=_items_payload(),
                created_by=USER_ID,
            )
        assert excinfo.value.error_code == "REVIEW_TEMPLATE_ARCHIVED"
        with pytest.raises(ConflictError):
            await svc.update_template(db, template=archived, name="新名字", updated_by=USER_ID)
        with pytest.raises(ConflictError) as again:
            await svc.archive_template(db, template=archived, user_id=USER_ID)
        assert again.value.error_code == "REVIEW_TEMPLATE_ALREADY_ARCHIVED"

    await _seed_ready_case(factory)
    with pytest.raises(ConflictError) as excinfo:
        await _create_case(factory, template_id=template.id)
    assert excinfo.value.error_code == "REVIEW_TEMPLATE_ARCHIVED"

    # 归档不删行：模板与版本仍在，历史案件快照完好。
    async with factory() as db:
        assert await db.get(ReviewTemplate, template.id) is not None
        count = (
            await db.execute(
                select(func.count(ReviewTemplateVersion.id)).where(
                    ReviewTemplateVersion.template_id == template.id
                )
            )
        ).scalar()
        assert count == 1


@pytest.mark.asyncio
async def test_corrupted_version_is_rejected_for_case(factory):
    """版本内容哈希不符 = 损坏/被篡改：拒绝建案（不回落到别的版本/进程清单）。

    变异点：resolve_template_items 不调用 version_items 校验 → 本用例红。
    """
    template, version = await _template_with_version(factory)
    async with factory() as db:
        row = await db.get(ReviewTemplateVersion, version.id)
        tampered = [dict(entry) for entry in row.items_json]
        tampered[-1] = {**tampered[-1], "keywords": ["被篡改"]}
        row.items_json = tampered  # payload_sha256 仍是旧值
        await db.commit()

    await _seed_ready_case(factory)
    with pytest.raises(ConflictError) as excinfo:
        await _create_case(factory, template_id=template.id)
    assert excinfo.value.error_code == "REVIEW_TEMPLATE_VERSION_CORRUPT"

    async with factory() as db:
        fetched = await svc.get_template_version(
            db, organization_id=ORG, template=template, version_no=version.version_no
        )
        data = svc.version_to_dict(fetched)
        assert data["intact"] is False


# ── 4. 上限与输入校验 ──────────────────────────────────────────────────────


def _custom(
    key: str = "confidentiality", keywords: list[str] | None = None, label: str = ""
) -> dict:
    return {"key": key, "keywords": keywords or ["保密"], "label": label}


@pytest.mark.parametrize(
    "items, field",
    [
        ([{"key": "Bad-Key", "keywords": ["x"]}], "items.key"),
        ([_custom("dup"), _custom("dup", ["x"])], "items.key"),
        ([{"key": "foo", "keywords": []}], "items.keywords"),
        ([{"key": "foo", "keywords": ["   "]}], "items.keywords"),
        ([{"key": "payment_term", "keywords": ["付款"]}], "items.key"),
    ],
)
def test_normalize_custom_rejects_invalid_items(items, field):
    """非法自定义项被服务层拒绝（内置 key 不得作为自定义项提交）。

    变异点：把对应校验删掉 → 该参数化用例不再抛错而变红。
    """
    with pytest.raises(ValidationError) as excinfo:
        svc.normalize_custom_items(items)
    assert excinfo.value.extra.get("field") == field


def test_normalize_rejects_non_list_custom_items():
    """自定义项必须是数组（None 视为空数组，其它类型拒绝）。"""
    with pytest.raises(ValidationError):
        svc.normalize_custom_items("not-a-list")  # type: ignore[arg-type]
    assert svc.normalize_custom_items(None) == []


def test_normalize_full_rejects_builtin_tampering_and_missing_builtins():
    """完整列表口径（bootstrap/内部）：内置项必须原样保留。

    变异点：删除 DEFAULT_ITEM_KEYWORDS 集合比对 → 改词用例变红；
    删除 missing 检查 → 删项用例变红。
    """
    tampered = _builtin_items()
    tampered[0] = {**tampered[0], "keywords": ["付款"]}
    with pytest.raises(ValidationError) as excinfo:
        svc.normalize_items(tampered)
    assert excinfo.value.extra.get("field") == "items.keywords"

    labeled = _builtin_items()
    labeled[0] = {**labeled[0], "label": "付款期限"}
    with pytest.raises(ValidationError) as excinfo:
        svc.normalize_items(labeled)
    assert excinfo.value.extra.get("field") == "items.label"

    with pytest.raises(ValidationError) as excinfo:
        svc.normalize_items(_builtin_items()[:-1])
    assert excinfo.value.extra.get("missing") == ["exception"]


def test_normalize_enforces_count_and_length_caps():
    """项数（含内置 4 项）/关键词数/关键词长度/label 长度上限。"""
    too_many_items = [
        {"key": f"item_{index}", "keywords": ["x"]} for index in range(svc.MAX_ITEMS_PER_VERSION)
    ]
    with pytest.raises(ValidationError):
        svc.normalize_custom_items(too_many_items)
    too_many_keywords = [
        {
            "key": "foo",
            "keywords": [f"kw{index}" for index in range(svc.MAX_KEYWORDS_PER_ITEM + 1)],
        },
    ]
    with pytest.raises(ValidationError):
        svc.normalize_custom_items(too_many_keywords)
    long_keyword = [{"key": "foo", "keywords": ["x" * (svc.MAX_KEYWORD_CHARS + 1)]}]
    with pytest.raises(ValidationError):
        svc.normalize_custom_items(long_keyword)
    long_label = [
        {"key": "foo", "keywords": ["x"], "label": "y" * (svc.MAX_LABEL_CHARS + 1)},
    ]
    with pytest.raises(ValidationError):
        svc.normalize_custom_items(long_label)


@pytest.mark.asyncio
async def test_duplicate_template_name_conflicts(factory):
    """同组织模板名唯一：重复创建 409（错误码稳定）；不同组织可以同名。

    变异点：去掉 uq_review_template_org_name 唯一约束 → IntegrityError 分支
    不再触发，本用例红。
    """
    async with factory() as db:
        await svc.create_template(db, organization_id=ORG, created_by=USER_ID, name="同名")
        with pytest.raises(ConflictError) as excinfo:
            await svc.create_template(db, organization_id=ORG, created_by=USER_ID, name="同名")
        assert excinfo.value.error_code == "REVIEW_TEMPLATE_NAME_CONFLICT"
        await svc.create_template(db, organization_id=OTHER_ORG, created_by=USER_ID, name="同名")


@pytest.mark.asyncio
async def test_version_budget_caps_rows(factory, monkeypatch):
    """单模板版本数上限：达到上限后新增版本被拒（防止无界增长）。

    变异点：删除 _ensure_version_budget → 本用例红。
    """
    monkeypatch.setattr(svc, "MAX_VERSIONS_PER_TEMPLATE", 2)
    template, _ = await _template_with_version(factory)
    async with factory() as db:
        fresh = await svc.get_template(db, organization_id=ORG, template_id=template.id)
        await svc.create_version(db, template=fresh, items=_items_payload(), created_by=USER_ID)
        with pytest.raises(ValidationError):
            await svc.create_version(db, template=fresh, items=_items_payload(), created_by=USER_ID)


@pytest.mark.asyncio
async def test_template_budget_caps_active_rows(factory, monkeypatch):
    """单组织活跃模板数上限；归档不占额度。

    变异点：删除 create_template 的活跃计数 → 本用例红。
    """
    monkeypatch.setattr(svc, "MAX_TEMPLATES_PER_ORG", 2)
    async with factory() as db:
        first = await svc.create_template(
            db, organization_id=ORG, created_by=USER_ID, name="模板一"
        )
        await svc.create_template(db, organization_id=ORG, created_by=USER_ID, name="模板二")
        with pytest.raises(ValidationError):
            await svc.create_template(db, organization_id=ORG, created_by=USER_ID, name="模板三")
        await svc.archive_template(db, template=first, user_id=USER_ID)
        # 归档后释放额度
        await svc.create_template(db, organization_id=ORG, created_by=USER_ID, name="模板三")


@pytest.mark.asyncio
async def test_bootstrap_from_process_checklist(factory, monkeypatch):
    """bootstrap 把进程级 checklist（含 REVIEW_CHECKLIST_JSON 追加项）固化成 v1。

    变异点：bootstrap 改读固定 4 项 → 追加项断言红。
    """
    monkeypatch.setenv(
        "REVIEW_CHECKLIST_JSON",
        json.dumps([{"key": "confidentiality", "keywords": ["保密"], "label": "保密义务"}]),
    )
    async with factory() as db:
        template, version = await svc.bootstrap_from_checklist(
            db, organization_id=ORG, created_by=USER_ID, name="迁移模板"
        )
    assert template.latest_version == 1
    assert version.source == "bootstrap"
    keys = [entry["key"] for entry in version.items_json]
    assert keys == [*_builtin_keys(), "confidentiality"]
    assert version.item_count == 5
    by_key = {entry["key"]: entry for entry in version.items_json}
    assert by_key["confidentiality"]["configurable"] is True
    assert by_key["payment_term"]["configurable"] is False


@pytest.mark.asyncio
async def test_update_template_and_clear_description(factory):
    """更新模板头：改名/清空说明生效，lock_version 递增。"""
    async with factory() as db:
        template = await svc.create_template(
            db, organization_id=ORG, created_by=USER_ID, name="旧名", description="说明"
        )
        updated = await svc.update_template(
            db, template=template, name="新名", clear_description=True, updated_by=USER_ID
        )
        assert updated.name == "新名"
        assert updated.description is None
        assert updated.lock_version == 2
