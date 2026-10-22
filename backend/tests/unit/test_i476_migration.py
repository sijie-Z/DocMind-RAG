"""issue #476：迁移 012_session_fork 的 sqlite 往返（真实 alembic 链）。

钉住四件事（issue §7 分层验证的"迁移测试"）：
1. **存量回填**：升级前已有的消息按 (created_at, id) 得到会话内 1..N 的 seq；
   chat_sessions.root_session_id 回填为自身 id（根会话 = 自身）；
2. **约束真实存在**：(session_id, seq) 唯一索引拦住重复序号（不是只写在模型里）；
3. **新列可用**：agent_tasks.session_id 可写；PAUSED 状态在 sqlite 上可写入，
   downgrade 时如实转成旧可恢复态（WAITING_HUMAN），不回滚成"运行中"；
4. **downgrade 可回放**：回到 011 后新列/约束被删除，再次 upgrade 12 不报错。

MySQL 侧的列类型/枚举顺序由 tests/integration/test_schema_path_parity.py
（alembic vs create_all 逐列比对）兜底；本用例只跑 sqlite，无外部服务依赖。
"""

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from app.core.config import settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "011_organization_settings"
NEW_REV = "012_session_fork"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(sql, params)
        conn.commit()


def _scalar(db_path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: {"notnull": bool(r[3]), "default": r[4]} for r in rows}


def _unique_index_columns(db_path: Path, table: str) -> list[tuple]:
    out: list[tuple] = []
    with sqlite3.connect(str(db_path)) as conn:
        indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
        for row in indexes:
            if not row[2]:
                continue
            cols = conn.execute(f"PRAGMA index_info({row[1]})").fetchall()
            out.append(tuple(c[2] for c in cols))
    return out


def test_i476_migration_backfills_and_round_trips(tmp_path, monkeypatch):
    db_path = tmp_path / "mig_i476.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    # 迁移链必须单头且新 revision 是 head（011 仍是其前驱）。
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [NEW_REV]
    chain = [rev.revision for rev in script.iterate_revisions(NEW_REV, "base")]
    assert chain.index(PREV_REV) == chain.index(NEW_REV) + 1

    # ── 升到 011，塞"存量数据"（模拟升级窗口前的库）────────────────────
    command.upgrade(cfg, PREV_REV)
    assert "seq" not in _columns(db_path, "chat_messages")
    assert "parent_session_id" not in _columns(db_path, "chat_sessions")
    assert "session_id" not in _columns(db_path, "agent_tasks")

    _exec(
        db_path,
        "INSERT INTO chat_sessions (id, user_id, title, status, message_count, created_at) "
        "VALUES (?, 1, 'legacy', 'ACTIVE', 3, '2026-01-01 00:00:00')",
        ("sess-legacy",),
    )
    # created_at 相同 → 回填必须用 id 作为次序 tiebreak（'b' 排在 'a' 后）
    for msg_id, created_at in (
        ("msg-b", "2026-01-01 00:00:00"),
        ("msg-a", "2026-01-01 00:00:00"),
        ("msg-c", "2026-01-01 00:01:00"),
    ):
        _exec(
            db_path,
            "INSERT INTO chat_messages "
            "(id, session_id, content, message_type, feedback, created_at) "
            "VALUES (?, ?, ?, 'USER', 0, ?)",
            (msg_id, "sess-legacy", f"content-{msg_id}", created_at),
        )

    # ── 升级到 012：回填 + 约束 ─────────────────────────────────────────
    command.upgrade(cfg, NEW_REV)

    seqs = {
        row[0]: row[1]
        for row in sqlite3.connect(str(db_path))
        .execute("SELECT id, seq FROM chat_messages ORDER BY id")
        .fetchall()
    }
    assert seqs == {"msg-a": 1, "msg-b": 2, "msg-c": 3}, (
        "存量消息必须按 (created_at, id) 回填为会话内 1..N"
    )
    assert _columns(db_path, "chat_messages")["seq"]["notnull"] is True
    assert ("session_id", "seq") in _unique_index_columns(db_path, "chat_messages"), (
        "唯一约束 (session_id, seq) 必须真实建出"
    )
    assert (
        _scalar(db_path, "SELECT root_session_id FROM chat_sessions WHERE id = 'sess-legacy'")
        == "sess-legacy"
    ), "存量会话 root_session_id 必须回填为自身"

    # 重复 seq 会被数据库拒绝（模型层约束不是摆设）
    try:
        _exec(
            db_path,
            "INSERT INTO chat_messages "
            "(id, session_id, seq, content, message_type, feedback, created_at) "
            "VALUES ('dup', 'sess-legacy', 1, 'x', 'USER', 0, '2026-01-01 00:00:00')",
        )
    except sqlite3.IntegrityError:
        pass
    else:  # pragma: no cover - 约束丢失时给出可读失败
        raise AssertionError("(session_id, seq) 唯一约束未生效：重复 seq 被写入")

    # 新列可写：agent_tasks.session_id + PAUSED 状态
    _exec(
        db_path,
        "INSERT INTO agent_tasks (id, user_id, status, query, session_id) "
        "VALUES ('task-clone', 1, 'PAUSED', 'q', 'sess-legacy')",
    )
    assert (
        _scalar(db_path, "SELECT session_id FROM agent_tasks WHERE id = 'task-clone'")
        == "sess-legacy"
    )

    # ── downgrade 回 011：新列/约束消失，PAUSED 如实转换 ────────────────
    command.downgrade(cfg, PREV_REV)
    assert "seq" not in _columns(db_path, "chat_messages")
    assert "parent_session_id" not in _columns(db_path, "chat_sessions")
    assert "session_id" not in _columns(db_path, "agent_tasks")
    assert ("session_id", "seq") not in _unique_index_columns(db_path, "chat_messages")
    assert _scalar(db_path, "SELECT status FROM agent_tasks WHERE id = 'task-clone'") == (
        "WAITING_HUMAN"
    ), "PAUSED 降级必须转成旧可恢复态，绝不回滚成运行中"
    # 业务数据（消息本身）原样保留
    assert _scalar(db_path, "SELECT COUNT(*) FROM chat_messages") == 3

    # 再次升级可重放
    command.upgrade(cfg, NEW_REV)
    assert _columns(db_path, "chat_messages")["seq"]["notnull"] is True
