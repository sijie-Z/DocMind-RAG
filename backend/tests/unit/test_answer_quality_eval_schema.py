"""迁移 043：答案质量评测运行 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 043 接在 042 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``answer_quality_eval_run`` 的列/索引/唯一键/外键与 ORM 模型完全一致；
- ``aggregate`` / ``results`` / ``error_code`` / ``completed_at`` 可空
  （失败运行保留机器码但不产生分数）；
- 除 ``created_at`` 外无额外 server_default（默认值由服务层提供）；
- downgrade 回滚到 042 后表消失，042 的对象不受影响（round-trip 不残留）；
- downgrade 源码不得逐条 drop_index（MySQL 1553 先例）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HEAD = "044_answer_quality_eval"
PREVIOUS = "043_session_tree"
TABLE = "answer_quality_eval_run"

ORG_CREATED_INDEX = "ix_answer_quality_eval_run_org_created"
ORG_USER_CREATED_INDEX = "ix_answer_quality_eval_run_org_user_created"
ORG_STATUS_INDEX = "ix_answer_quality_eval_run_org_status"
CONFIG_UNIQUE = "uq_answer_quality_eval_run_config"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_044_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {HEAD}, "043 必须是当前唯一 head"


def test_044_downgrade_does_not_drop_indexes_before_tables():
    """downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / "044_answer_quality_eval.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(TABLE)" in downgrade
    assert "op.drop_index(" not in downgrade


def test_044_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级到 044 读回列/FK/索引/唯一键；回滚到 043 后表消失且 043 对象仍在。"""
    db_path = tmp_path / "answer-quality-eval-044.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert TABLE in tables
    # 042 的三张 rubric 表不能被 043 的 upgrade 误删。
    assert {"eval_rubric", "eval_rubric_version", "eval_rubric_run"} <= tables

    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert set(cols) == {c.name for c in Base.metadata.tables[TABLE].columns}, (
            "answer_quality_eval_run 迁移列与 ORM 模型漂移"
        )
        assert cols["created_at"][3] == 1, "created_at NOT NULL"
        assert cols["created_at"][4] is not None, "created_at 需要 server_default"
        # 失败运行必须能落库：分数列与完成时间可空。
        assert cols["aggregate"][3] == 0
        assert cols["results"][3] == 0
        assert cols["error_code"][3] == 0
        assert cols["completed_at"][3] == 0
        # 其余列不得偷偷带 DDL 默认值（由服务层/ORM 提供）。
        for name in ("mode", "status", "case_count", "prompt_version", "judge_model"):
            assert cols[name][4] is None, f"{name} 不应有 server_default"

        fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({TABLE})")}
        assert ("organizations", "organization_id") in fks
        assert ("users", "user_id") in fks
        assert ("users", "created_by") in fks

        indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({TABLE})")}
        assert {
            ORG_CREATED_INDEX,
            ORG_USER_CREATED_INDEX,
            ORG_STATUS_INDEX,
        } <= indexes

        # SQLite 把命名 UniqueConstraint 渲染成 sqlite_autoindex_*：按列+唯一性反查。
        unique_cols = {
            tuple(
                info[2]
                for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
                if info[2] is not None
            )
            for index_row in conn.execute(f"PRAGMA index_list({TABLE})")
            if index_row[2]
        }
        assert (
            "organization_id",
            "user_id",
            "dataset_hash",
            "judge_model",
            "prompt_version",
            "mode",
            "status",
        ) in unique_cols

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    tables = _tables(db_path)
    assert TABLE not in tables
    assert {"eval_rubric", "eval_rubric_version", "eval_rubric_run"} <= tables


def test_043_model_is_registered():
    """模型必须在 app.models 注册（create_all / schema guard 依赖它）。"""
    import app.models as models
    from app.models.answer_quality_eval import AnswerQualityEvalRun

    assert models.AnswerQualityEvalRun is AnswerQualityEvalRun
    assert "AnswerQualityEvalRun" in models.__all__
