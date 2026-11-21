"""合同审查风险等级 + 可解释性：判别性回归（竞品对标 P0）。

覆盖：facts.reason → severity、结构化引用、无正文/提示词泄漏、
旧数据/缺字段安全回落、导出 Markdown 风险标签、030 迁移回填。
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

from alembic import command
from app.core.config import settings
from app.services import review_export_service as export_service
from app.services.review_explanation_service import derive_explanation, derive_severity

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


def test_severity_falls_back_to_change_type_and_existing_value() -> None:
    assert derive_severity(None, change_type="modified") == "medium"
    assert derive_severity(None, change_type="unchanged") == "low"
    assert derive_severity({}, change_type=None) == "low"
    assert derive_severity(None, severity="high") == "high"
    assert derive_severity(None, severity="invalid") == "low"


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
    assert heads == {"031_review_severity"}
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert chain[chain.index("031_review_severity") + 1] == "029_chat_session_share_token"

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
