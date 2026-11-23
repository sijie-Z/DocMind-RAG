"""合同审查风险等级 + 可解释性：判别性回归（竞品对标 P0）。

覆盖：facts.reason → severity、结构化引用、无正文/提示词泄漏、
旧数据/缺字段安全回落、导出 Markdown 风险标签、031 迁移回填。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # 初始化 Base.metadata
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.review import ReviewCase, ReviewDocumentMeta, ReviewItem
from app.services import review_analysis_service as analysis_service
from app.services import review_case_service as case_service
from app.services import review_export_service as export_service
from app.services.review_explanation_service import (
    derive_explanation,
    derive_review_explanation,
    derive_severity,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"base": {"status": "ambiguous", "reason": "multiple_durations"}}, "high"),
        ({"base": {"status": "ambiguous", "reason": "reference_unresolved"}}, "high"),
        (
            {"base": {"status": "ambiguous", "reason": "unit_unclear+multiple_durations"}},
            "high",
        ),
        ({"revised": {"status": "not_found", "reason": "no_duration"}}, "medium"),
        ({"base": {"status": "ambiguous", "reason": "unit_unclear"}}, "medium"),
        ({"base": {"status": "not_found", "reason": "no_terms"}}, "medium"),
        ({"base": {"status": "identified", "value": 30, "unit": "day"}}, "low"),
        ({"base": {"status": "identified", "reason": "future_unknown_code"}}, "low"),
    ],
)
def test_severity_derived_from_facts_reason(facts: dict[str, Any], expected: str) -> None:
    """删掉 facts.reason → severity 派生逻辑，本用例必须变红。"""
    assert derive_severity(facts) == expected


def test_severity_falls_back_to_change_type() -> None:
    assert derive_severity(None, change_type="modified") == "medium"
    assert derive_severity(None, change_type="unchanged") == "low"
    assert derive_severity({}, change_type=None) == "low"

    # 旧值透传回归：已有派生值不得覆盖当前 facts（schema 无人工覆盖字段）。
    current_high = {"base": {"status": "ambiguous", "reason": "multiple_durations"}}
    assert (
        derive_review_explanation({"facts": {}, "change_type": "unchanged", "severity": "high"})[0]
        == "low"
    )
    assert (
        derive_review_explanation(
            {"facts": current_high, "change_type": "unchanged", "severity": "low"}
        )[0]
        == "high"
    )


def test_explanation_has_structured_references_without_text_leak() -> None:
    """删掉结构化 explanation 构造，本用例必须变红。"""
    facts = {
        "base": {"status": "ambiguous", "reason": "unit_unclear"},
        "revised": {"status": "not_found", "reason": "reference_unresolved"},
    }
    evidence = {
        "base": [
            {
                "id": "candidate-id",
                "selected": False,
                "role": "candidate",
                "quote": "机密原文 第8条 付款期限为30天",
            },
            {
                "id": "main-id",
                "selected": True,
                "role": "main",
                "quote": "不应泄漏的正文\n## 注入",
            },
        ],
        "revised": [],
    }
    explanation = derive_explanation(facts, item_key="payment_term", evidence=evidence)
    assert explanation == {
        "rule_id": "payment_term",
        "evidence_id": "main-id",
        "reason_code": "unit_unclear+reference_unresolved",
    }
    serialized = json.dumps(explanation, ensure_ascii=False)
    for leak in ("机密原文", "不应泄漏", "付款期限", "## 注入", "quote", "prompt"):
        assert leak not in serialized


@pytest.mark.parametrize(
    ("hostile_reason", "expected_code"),
    [
        ("unit_unclear+SENTINEL_DO_NOT_LEAK", "unit_unclear"),
        ("# SENTINEL_DO_NOT_LEAK\n## ignored", None),
        ("‮SENTINEL_DO_NOT_LEAK", None),
        ("SENTINEL_DO_NOT_LEAK" * 60000, None),  # > 1MB 串
    ],
    ids=["mixed-known-and-hostile", "markdown-injection", "bidi-control", "oversized"],
)
def test_reason_code_whitelist_blocks_hostile_text_everywhere(
    hostile_reason: str, expected_code: str | None
) -> None:
    """未知/敌意/超长 reason 不得进入 explanation 或 Markdown 导出。"""
    facts = {"base": {"status": "ambiguous", "reason": hostile_reason}}
    explanation = derive_explanation(facts, item_key="payment_term")
    assert explanation is not None
    assert explanation["reason_code"] == expected_code
    assert "SENTINEL_DO_NOT_LEAK" not in json.dumps(explanation, ensure_ascii=False)
    assert derive_severity(facts, change_type="unchanged") == ("medium" if expected_code else "low")

    payload = {
        "schema_version": 1,
        "case": {"id": "case-1", "title": "白名单用例", "checklist_version": "v1"},
        "documents": {},
        "items": [
            {
                "item_key": "payment_term",
                "change_type": "unchanged",
                "status": "verified",
                "facts": facts,
                "evidence": {"base": [], "revised": [], "policy": []},
            }
        ],
        "coverage": {},
        "source_stale": False,
        "disclaimer": "免责声明",
    }
    markdown = export_service.render_markdown(payload)
    assert "SENTINEL_DO_NOT_LEAK" not in markdown
    assert "‮" not in markdown
    assert "## ignored" not in markdown


@pytest.mark.parametrize(
    "args",
    [
        {"item_key": None, "evidence": None, "facts": None},
        {"item_key": "", "evidence": {}, "facts": {}},
        {"item_key": "unknown_key", "evidence": {"base": [{"quote": "只有正文"}]}, "facts": None},
    ],
)
def test_explanation_safe_fallback(args: dict[str, Any]) -> None:
    explanation = derive_explanation(**args)
    assert explanation is None or (
        set(explanation) == {"rule_id", "evidence_id", "reason_code"}
        and explanation.get("evidence_id") is None
    )


def test_render_markdown_includes_severity_label_and_no_body_leak() -> None:
    """旧 payload（无 severity/explanation 字段）也必须显示派生风险标签。"""
    payload = {
        "schema_version": 1,
        "case": {"id": "case-1", "title": "回归案件", "checklist_version": "v1"},
        "documents": {},
        "items": [
            {
                "item_key": "payment_term",
                "change_type": "modified",
                "status": "needs_human",
                "decided_by": None,
                "decided_at": None,
                "decision_note": None,
                "facts": {"base": {"status": "ambiguous", "reason": "multiple_durations"}},
                "evidence": {
                    "base": [
                        {
                            "id": "evidence-1",
                            "selected": True,
                            "role": "main",
                            "quote": "不得出现在风险依据里的正文",
                            "clause_no": [],
                        }
                    ],
                    "revised": [],
                    "policy": [],
                },
            },
            {
                "item_key": "exception",
                "change_type": "unchanged",
                "status": "verified",
                "facts": {},
                "evidence": {"base": [], "revised": [], "policy": []},
            },
        ],
        "coverage": {},
        "source_stale": False,
        "disclaimer": "免责声明",
    }
    markdown = export_service.render_markdown(payload)
    assert "高风险" in markdown, "导出文本必须含 severity 标签"
    assert "- 风险等级：高风险" in markdown
    assert "证据：evidence-1" in markdown
    assert "规则：付款期限" in markdown
    assert "- 风险等级：低风险" in markdown
    risk_block = markdown.split("## 证据（原文引用）", maxsplit=1)[0]
    assert "不得出现在风险依据里的正文" not in risk_block


@pytest.fixture
async def reanalysis_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'severity-reanalysis.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_reanalysis_refreshes_severity_and_explanation(reanalysis_factory) -> None:
    """重新分析改写 facts 后，派生字段必须跟着变，不得透传旧值。"""
    docs: dict[str, str] = {}
    async with reanalysis_factory() as db:
        # key = review side（policy/base/revised），doc_role 单独写进元数据。
        for side, doc_id, role, family, label, seq in (
            ("policy", "sev-policy", "policy", None, None, None),
            ("base", "sev-base", "contract", "sev-family", "V1", 1),
            ("revised", "sev-revised", "contract", "sev-family", "V2", 2),
        ):
            docs[side] = doc_id
            db.add(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.pdf",
                    file_path=f"1/{doc_id}.pdf",
                    file_size=100,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=1,
                    uploaded_by=7,
                )
            )
            db.add(
                ReviewDocumentMeta(
                    document_id=doc_id,
                    organization_id=1,
                    doc_role=role,
                    family_key=family,
                    version_label=label,
                    version_seq=seq,
                    content_sha256=doc_id[0] * 64,
                    registered_by=7,
                )
            )
        db.add_all(
            [
                DocumentChunk(
                    id=str(uuid4()),
                    document_id=docs["policy"],
                    chunk_index=0,
                    chunk_text="第3条 采购付款应遵循本制度，另有约定的从其约定。",
                    original_text="第3条 采购付款应遵循本制度，另有约定的从其约定。",
                    chunk_length=20,
                    start_pos=0,
                    end_pos=20,
                    page_number=1,
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentChunk(
                    id=str(uuid4()),
                    document_id=docs["base"],
                    chunk_index=0,
                    chunk_text="第八条 付款期限为30天，自收到发票之日起算。",
                    original_text="第八条 付款期限为30天，自收到发票之日起算。",
                    chunk_length=20,
                    start_pos=0,
                    end_pos=20,
                    page_number=1,
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentChunk(
                    id=str(uuid4()),
                    document_id=docs["revised"],
                    chunk_index=0,
                    chunk_text="第八条 付款分两期：首款30天，尾款60天，均自验收合格起算。",
                    original_text="第八条 付款分两期：首款30天，尾款60天，均自验收合格起算。",
                    chunk_length=20,
                    start_pos=0,
                    end_pos=20,
                    page_number=1,
                    meta_data={"text_provenance": "parser_original"},
                ),
            ]
        )
        await db.commit()
        document_rows = {role: await db.get(Document, doc_id) for role, doc_id in docs.items()}
        case = await case_service.create_case(
            db,
            docs=document_rows,
            title="reanalysis case",
            checklist_version="v1",
            created_by=7,
        )
        case_id = case.id
        first = await analysis_service.analyze_case(db, case)

    def _payment(detail):
        return next(item for item in detail["items"] if item["item_key"] == "payment_term")

    before = _payment(first)
    assert before["facts"]["revised"]["status"] == "ambiguous"
    assert before["severity"] == "high"
    assert "multiple_durations" in before["explanation"]["reason_code"]

    async with reanalysis_factory() as db:
        chunk = (
            await db.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == docs["revised"])
            )
        ).scalar_one()
        new_text = "第八条 付款期限为30天，自收到发票之日起算。"
        chunk.original_text = new_text
        chunk.chunk_text = new_text
        await db.commit()
        case = await db.get(ReviewCase, case_id)
        second = await analysis_service.analyze_case(db, case)

    after = _payment(second)
    assert after["facts"]["revised"]["status"] == "identified"
    # 旧的 multiple_durations 必须消失；policy 侧仍有 no_duration，故重算为 medium。
    assert "multiple_durations" not in (after["explanation"]["reason_code"] or "")
    assert after["severity"] == "medium"
    async with reanalysis_factory() as db:
        row = (
            await db.execute(
                select(ReviewItem).where(
                    ReviewItem.case_id == case_id, ReviewItem.item_key == "payment_term"
                )
            )
        ).scalar_one()
    assert row.severity == "medium"
    assert "multiple_durations" not in (row.explanation["reason_code"] or "")


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_031_migration_chain_and_backfill(monkeypatch) -> None:
    """031 接在 029 之后；旧行回填 high/medium，explanation 只含机器引用。"""
    db_path = BACKEND_ROOT / ".scratch" / "pytest" / f"severity-031-{uuid4().hex}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    assert heads == {"037_index_error_codes"}
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert chain[chain.index("031_review_severity") + 1] == "030_cost_attribution"

    command.upgrade(cfg, "029_chat_session_share_token")
    case_id, item_id, ev_id = str(uuid4()), str(uuid4()), str(uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    # 回填只读 review_item / review_evidence；SQLite 未开启外键强制，
    # 无需为这条回归伪造 org/user/case 行（它们的必填列随版本变迁）。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO review_item (id, case_id, item_key, ordinal, status, facts, "
            "change_type, created_at, updated_at) VALUES (?, ?, 'payment_term', 0, 'pending', "
            "?, 'modified', ?, ?)",
            (
                item_id,
                case_id,
                json.dumps({"base": {"status": "ambiguous", "reason": "multiple_durations"}}),
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO review_evidence (id, case_id, item_id, side, role, selected, "
            "document_id, doc_role, document_sha256, chunk_id_at_capture, chunk_index, "
            "quote_text, quote_sha256, text_provenance, captured_at) "
            "VALUES (?, ?, ?, 'base', 'main', 1, 'doc', 'contract', ?, 'chunk', 0, "
            "'正文', ?, 'parser_original', ?)",
            (ev_id, case_id, item_id, "d" * 64, "q" * 64, now),
        )

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT severity, explanation FROM review_item WHERE id = ?", (item_id,)
        ).fetchone()
    assert row[0] == "high"
    assert json.loads(row[1]) == {
        "rule_id": "payment_term",
        "evidence_id": ev_id,
        "reason_code": "multiple_durations",
    }
    command.downgrade(cfg, "029_chat_session_share_token")
    with sqlite3.connect(str(db_path)) as conn:
        cols = {entry[1] for entry in conn.execute("PRAGMA table_info('review_item')")}
    assert "severity" not in cols and "explanation" not in cols
