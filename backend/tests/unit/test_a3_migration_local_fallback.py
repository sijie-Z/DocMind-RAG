"""A3 迁移 010 接在 A2 之后：单 head 链 009→A2→A3 与逐级 downgrade（SQLite）。

预审阻塞：A3 与 A2 的迁移都挂 009 会产生两个 head，``alembic upgrade head``
直接失败。本用例钉住：

1. 合并后 revision 链只有一个 head，且 head = A3；
2. 009→A2→A3 一次升级成功，A2/A3 的新列都存在；
3. 逐级 downgrade：先退 A3（只删 local_fallback，A2 列仍在），再退 A2；
4. 重新升级到 head 可重放（downgrade 可逆）。
"""

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "009_agent_task_plan_approval"
A2_REV = "010_tenant_boundary_org"
A3_REV = "010_knowledge_job_local_fallback"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _exec(db_path, statement: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _scalar(db_path, statement: str):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(statement).fetchone()[0]
    finally:
        conn.close()


def _columns(db_path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_a3_migration_single_head_chain_and_stepwise_downgrade(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    db_path = tmp_path / "mig_a3.db"
    monkeypatch.setattr(
        settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}"
    )
    cfg = _cfg()

    # 单一 head，且是我们的迁移（A2+A3 合并后不允许出现两个 head）
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [A3_REV]

    command.upgrade(cfg, PREV_REV)
    assert "local_fallback" not in _columns(db_path, "knowledge_processing_jobs")

    # 009→A2→A3
    command.upgrade(cfg, "head")
    assert "local_fallback" in _columns(db_path, "knowledge_processing_jobs")
    assert "organization_id" in _columns(db_path, "workflows")

    # 服务端默认值：按旧形态插入（不写 local_fallback）→ 0（旧进程兼容）
    _exec(
        db_path,
        """
        INSERT INTO knowledge_processing_jobs
            (document_id, organization_id, trigger_type, status, retry_count)
        VALUES ('doc-old', 1, 'upload', 'QUEUED', 0)
        """,
    )
    assert (
        _scalar(
            db_path,
            "SELECT CAST(local_fallback AS INTEGER) FROM knowledge_processing_jobs "
            "WHERE document_id = 'doc-old'",
        )
        == 0
    )

    # 逐级 downgrade：先 A3——只删 local_fallback，A2 列保留
    command.downgrade(cfg, A2_REV)
    assert "local_fallback" not in _columns(db_path, "knowledge_processing_jobs")
    assert "organization_id" in _columns(db_path, "workflows")

    # 再 A2——workflows/prompt_templates 的组织列消失
    command.downgrade(cfg, PREV_REV)
    assert "organization_id" not in _columns(db_path, "workflows")
    assert "organization_id" not in _columns(db_path, "prompt_templates")

    # 可重放：再次升到 head
    command.upgrade(cfg, "head")
    assert "local_fallback" in _columns(db_path, "knowledge_processing_jobs")
    assert "organization_id" in _columns(db_path, "workflows")
