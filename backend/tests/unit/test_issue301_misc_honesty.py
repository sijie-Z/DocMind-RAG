"""issue #301（杂项批）判别用例：#24 / #25 / #32（#31 见 tests/api/）。

覆盖三条「假成功/静默吞」家族项：
- #24 `notifications.py`：非缺列数据库故障不得被吞成 200 形态；缺列回退须 warning 留痕；
  **评审 B1**：判定只认消息主体段的短语+精确列名——真实渲染文本（含
  `[SQL:]`/`[parameters:]` 回显）下的瞬时故障不得被误诊为缺列（忠实/对抗用例）；
- #25 `llm_config.py`：坏配置值不得凭空消失——保留 invalid 占位条目 + warning；
- #32 `core/prometheus.py`：指标构建失败须 ERROR 留痕（恰一次）+ /metrics 自曝不可用。
"""
import datetime
import json
import logging
import os
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.api.v1.endpoints.llm_config as llm_config_module
from app.api.v1.endpoints.notifications import (
    _is_legacy_missing_route_columns,
    get_notifications,
)

_NOTE = "app.api.v1.endpoints.notifications"


class _RowResult:
    """伪装 SQLAlchemy Result：按需支持 scalar() / all() / scalars().all()。"""

    def __init__(self, *, scalar=None, rows=None, orm_rows=None):
        self._scalar = scalar
        self._rows = [] if rows is None else rows
        self._orm_rows = [] if orm_rows is None else orm_rows

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._orm_rows)


class _ScriptedSession:
    """按调用序号吐剧本：第 1 次抛 exc（None 则不抛），此后依次弹出 results。"""

    def __init__(self, exc, results):
        self._exc = exc
        self._results = list(results)
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        if self.calls == 1 and self._exc is not None:
            raise self._exc
        return self._results.pop(0)


async def _call_list(db):
    return await get_notifications(
        skip=0, limit=20, unread_only=False, type=None, q=None,
        current_user=SimpleNamespace(id=7), db=db,
    )


# ── 真实渲染异常夹具（评审 B1/m3：判别用例必须喂真异常文本，而非手搭串）──

_LEGACY_NOTIFICATIONS_SCHEMA = """
CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  type VARCHAR(50) NOT NULL DEFAULT 'system',
  is_read BOOLEAN NOT NULL DEFAULT 0,
  created_at DATETIME
);
"""

_FULL_NOTIFICATIONS_SCHEMA = """
CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  type VARCHAR(50) NOT NULL DEFAULT 'system',
  is_read BOOLEAN NOT NULL DEFAULT 0,
  target_route VARCHAR(100),
  target_id VARCHAR(100),
  created_at DATETIME
);
INSERT INTO notifications (user_id, title, content, type, is_read, target_route, target_id, created_at)
VALUES (7, 'hello', 'world', 'system', 0, 'Knowledge', '1', '2026-01-01 00:00:00');
INSERT INTO notifications (user_id, title, content, type, is_read, target_route, target_id, created_at)
VALUES (1054, 'hello', 'world', 'system', 0, 'Knowledge', '2', '2026-01-01 00:00:00');
"""


async def _capture_real_endpoint_error(
    tmp_path, *, legacy: bool, lock: bool, user_id: int, q: str | None
):
    """在真 sqlite 上跑端点同款查询（count 子查询 + 列表查询），捕获真实 SQLAlchemy 异常。

    - `legacy=True`：旧形态表（无 target_route/target_id）→ 真缺列异常；
    - `lock=True`：独立连接 BEGIN EXCLUSIVE 制造真锁故障（评审 p1 手法），
      q/user_id 走端点原样参数化路径，渲染文本含真实 `[SQL:]`/`[parameters:]` 回显。
    """
    dbf = os.path.join(
        str(tmp_path),
        f"notif_{'legacy' if legacy else 'full'}_{'lock' if lock else 'ok'}_{user_id}.db",
    )
    conn = sqlite3.connect(dbf)
    conn.executescript(
        _LEGACY_NOTIFICATIONS_SCHEMA if legacy else _FULL_NOTIFICATIONS_SCHEMA
    )
    conn.commit()
    conn.close()
    locker = None
    if lock:
        locker = sqlite3.connect(dbf)
        locker.execute("BEGIN EXCLUSIVE")

    from sqlalchemy import desc, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.notification import Notification

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbf}", connect_args={"timeout": 0.05}
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            base_query = select(Notification).where(Notification.user_id == user_id)
            if q:
                keyword = f"%{q}%"
                base_query = base_query.where(
                    (Notification.title.like(keyword))
                    | (Notification.content.like(keyword))
                )
            total_result = await session.execute(
                select(func.count()).select_from(base_query.subquery())
            )
            total_result.scalar()
            await session.execute(
                base_query.order_by(desc(Notification.created_at)).limit(20).offset(0)
            )
            return None
    except Exception as exc:  # 真异常对象（含真渲染文本）
        return exc
    finally:
        if locker is not None:
            locker.rollback()
            locker.close()
        await engine.dispose()


class TestLegacyColumnDetectionB1:
    """评审 B1：判定只认「消息主体段」的短语+精确列名，抗 SQL/参数回显污染。"""

    @pytest.mark.asyncio
    async def test_real_legacy_error_is_detected(self, tmp_path):
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=True, lock=False, user_id=7, q=None
        )
        assert exc is not None, "预期真旧库查询抛异常"
        assert _is_legacy_missing_route_columns(exc) is True

    @pytest.mark.asyncio
    async def test_real_legacy_error_detected_without_orig_via_stripped_body(self, tmp_path):
        """无 DBAPI 原文（.orig）时，剥掉 [SQL:]/[parameters:] 段的回退路径同样可判。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=True, lock=False, user_id=7, q=None
        )
        rendered_without_orig = Exception(str(exc))  # 同一真实渲染文本，但无 .orig
        assert _is_legacy_missing_route_columns(rendered_without_orig) is True

    @pytest.mark.asyncio
    async def test_transient_lock_with_1054_q_is_not_legacy(self, tmp_path):
        """q='1054'（用户可控）的真实锁故障：渲染回显里确有 1054，但判定必须为 False。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=7, q="1054"
        )
        assert exc is not None
        assert "1054" in str(exc), "真实渲染回显里确实带着 1054（B1 的污染源）"
        assert _is_legacy_missing_route_columns(exc) is False

    @pytest.mark.asyncio
    async def test_transient_lock_with_structural_user_id_1054_is_not_legacy(self, tmp_path):
        """无任何攻击输入：user_id=1054 结构性出现在参数回显——同样不得误诊。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=1054, q=None
        )
        assert exc is not None
        assert "1054" in str(exc)
        assert _is_legacy_missing_route_columns(exc) is False

    @pytest.mark.asyncio
    async def test_forged_phrase_in_params_is_not_legacy(self, tmp_path):
        """参数值里塞完整伪造短语（unknown column 'target_route'）也不得误诊。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=7,
            q="unknown column 'target_route'",
        )
        assert exc is not None
        assert "unknown column" in str(exc)
        assert _is_legacy_missing_route_columns(exc) is False

    @pytest.mark.asyncio
    async def test_forged_phrase_without_orig_via_stripped_body_is_not_legacy(self, tmp_path):
        """无 .orig 的回退路径：剥段后伪造短语同样不可见。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=7,
            q="unknown column 'target_route'",
        )
        rendered_without_orig = Exception(str(exc))
        assert _is_legacy_missing_route_columns(rendered_without_orig) is False

    def test_mysql_1054_orig_detected_despite_noisy_echoes(self):
        """MySQL 形态：驱动原文含 1054 短语，SQL/参数回显带杂音也不影响判定。"""
        exc = ProgrammingError(
            "SELECT notifications.target_route FROM notifications WHERE user_id = 1054",
            {"q": "%1054%"},
            Exception(
                "(1054, \"Unknown column 'notifications.target_route' in 'field list'\")"
            ),
        )
        assert _is_legacy_missing_route_columns(exc) is True

    def test_other_missing_column_is_not_legacy(self):
        """别的列缺失（如 is_read）不是目标场景——不得触发回退。"""
        exc = ProgrammingError(
            "SELECT notifications.is_read FROM notifications",
            {"user_id": 1054},
            Exception("(1054, \"Unknown column 'notifications.is_read' in 'field list'\")"),
        )
        assert _is_legacy_missing_route_columns(exc) is False


class TestNotificationsFallbackHonesty:
    """#24：回退查询不得吞任意故障；缺列回退不得零留痕（B1 对抗含入端点层）。"""

    @pytest.mark.asyncio
    async def test_db_outage_is_not_masked_as_success(self):
        """连接丢失（非缺列）必须上抛（全局处理 → 500），不再伪装成
        「查询成功、只是没有路由字段」的 200 形态。"""
        exc = OperationalError(
            "SELECT notifications.* FROM notifications",
            {},
            Exception("(2006, 'MySQL server has gone away')"),
        )
        with pytest.raises(OperationalError):
            await _call_list(_ScriptedSession(exc, []))

    @pytest.mark.asyncio
    async def test_real_locked_fault_with_1054_q_is_not_masked(self, tmp_path):
        """评审 B1 对抗：q='1054' 的真实锁故障必须如实上抛（绝不被误诊为缺列）。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=7, q="1054"
        )
        assert exc is not None
        db = _ScriptedSession(
            exc, [_RowResult(scalar=1), _RowResult(scalar=1), _RowResult(rows=[])]
        )
        with pytest.raises(OperationalError):
            await _call_list(db)

    @pytest.mark.asyncio
    async def test_real_locked_fault_with_structural_user_id_1054_is_not_masked(self, tmp_path):
        """评审 B1 对抗：user_id=1054 的真实锁故障必须如实上抛。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=False, lock=True, user_id=1054, q=None
        )
        assert exc is not None
        db = _ScriptedSession(
            exc, [_RowResult(scalar=1), _RowResult(scalar=1), _RowResult(rows=[])]
        )
        with pytest.raises(OperationalError):
            await _call_list(db)

    @pytest.mark.asyncio
    async def test_real_legacy_missing_columns_fallback_is_logged(self, tmp_path, caplog):
        """真旧库（真缺列异常）→ 回退可用（字段保真为 None），且必须有 warning 留痕。"""
        exc = await _capture_real_endpoint_error(
            tmp_path, legacy=True, lock=False, user_id=7, q=None
        )
        assert exc is not None
        rows = [(1, "文档已就绪", "c1", "document", False, datetime.datetime(2026, 1, 1))]
        db = _ScriptedSession(
            exc, [_RowResult(scalar=1), _RowResult(scalar=1), _RowResult(rows=rows)]
        )
        with caplog.at_level(logging.WARNING, logger=_NOTE):
            out = await _call_list(db)

        assert out["total"] == 1
        assert out["items"][0]["target_route"] is None
        assert out["items"][0]["target_id"] is None
        warnings = [
            r for r in caplog.records
            if r.name == _NOTE and r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1, "缺列回退必须恰留一条 warning（原 bug 为零留痕）"
        assert "target_route" in warnings[0].getMessage()

    @pytest.mark.asyncio
    async def test_normal_path_keeps_route_fields(self):
        """回归：正常库路径不受影响，target_route/target_id 原样透出。"""
        n = SimpleNamespace(
            id=1, title="t", content="c", type="document", is_read=False,
            target_route="Knowledge", target_id="42",
            created_at=datetime.datetime(2026, 1, 1),
        )
        db = _ScriptedSession(
            None,
            [_RowResult(scalar=1), _RowResult(scalar=1), _RowResult(orm_rows=[n])],
        )
        out = await _call_list(db)
        assert out["items"][0].target_route == "Knowledge"
        assert out["total"] == 1


class _FakeRedis:
    def __init__(self, mapping):
        self._mapping = mapping

    async def hgetall(self, key):
        return self._mapping


_GOOD_JSON = json.dumps({
    "id": "good00000001", "provider": "deepseek",
    "config_name": "ok", "api_key": "sk-abcdefghijklmnop",
})
_BAD_RAW = '{"provider": "deepseek", "api_key": "sk-SECRET-halfwritten'


class TestLlmConfigCorruptValue:
    """#25：坏配置值保留为 invalid 占位条目，不静默丢弃。"""

    @pytest.mark.asyncio
    async def test_corrupt_value_kept_and_marked(self, monkeypatch, caplog):
        monkeypatch.setattr(
            llm_config_module, "redis_client",
            _FakeRedis({"good00000001": _GOOD_JSON, "bad000000002": _BAD_RAW}),
        )
        with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.llm_config"):
            result = await llm_config_module._load_all()

        assert set(result) == {"good00000001", "bad000000002"}, "坏条目不得凭空消失（原 bug：少 1 条）"
        bad = result["bad000000002"]
        assert bad["invalid"] is True
        assert bad["invalid_reason"], "必须带可观测的损坏原因"
        assert "bad000000002" in bad["config_name"]
        assert result["good00000001"]["provider"] == "deepseek", "好条目不受影响"

        warnings = [
            r for r in caplog.records
            if r.name == "app.api.v1.endpoints.llm_config" and r.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1, "坏值必须 warning 留痕（原 bug 为零留痕）"

    @pytest.mark.asyncio
    async def test_corrupt_public_view_never_leaks_raw(self, monkeypatch):
        """公共视图必须剥离原始值（可能含半写的 api_key），且好条目密钥继续脱敏。"""
        monkeypatch.setattr(
            llm_config_module, "redis_client",
            _FakeRedis({"good00000001": _GOOD_JSON, "bad000000002": _BAD_RAW}),
        )
        result = await llm_config_module._load_all()

        public = llm_config_module._public_config(result["bad000000002"])
        assert "_raw_value" not in public
        assert "sk-SECRET" not in json.dumps(public, ensure_ascii=False)
        assert public["invalid"] is True

        good_public = llm_config_module._public_config(result["good00000001"])
        assert good_public["api_key"] != "sk-abcdefghijklmnop"

    @pytest.mark.asyncio
    async def test_non_object_json_value_is_invalid(self, monkeypatch):
        """合法 JSON 但非对象（如数组）同样按损坏处理，不再静默丢。"""
        monkeypatch.setattr(
            llm_config_module, "redis_client", _FakeRedis({"list00000001": "[1, 2, 3]"}),
        )
        result = await llm_config_module._load_all()
        assert result["list00000001"]["invalid"] is True


class TestPrometheusBuildFailureIsExplicit:
    """#32：构建失败必显形（ERROR 恰一次 + /metrics 自曝行），未启用态不受影响。"""

    def _pin_failure(self, monkeypatch):
        from app.core import prometheus as p

        monkeypatch.setattr(p, "_real_metrics", None)
        monkeypatch.setattr(p, "_build_failure", None)
        monkeypatch.setattr(p, "_build_failure_logged", False)
        monkeypatch.setattr(p, "_METRICS_ENABLED", True)

        def _boom():
            raise ValueError("Duplicated timeseries in CollectorRegistry: {'rag_retrieval_total'}")

        monkeypatch.setattr(p, "_build_metrics", _boom)
        return p

    def test_build_failure_logged_once_and_exported_as_unavailable(self, monkeypatch, caplog):
        p = self._pin_failure(monkeypatch)
        with caplog.at_level(logging.ERROR, logger="app.core.prometheus"):
            p.AGENT_TOOL_CALLS.inc()  # 埋点保持 no-op、不抛
            p.AGENT_TOOL_CALLS.observe(0.1)
            p.AGENT_TOOL_CALLS.labels(tool="t", result="ok").inc()
            text = p.get_prometheus_metrics()

        errors = [
            r for r in caplog.records
            if r.name == "app.core.prometheus" and r.levelno >= logging.ERROR
        ]
        assert len(errors) == 1, "构建失败必须 ERROR 留痕且每进程只打一次"
        assert b"docmind_metrics_unavailable 1" in text, "/metrics 必须自曝不可用，而非静默少整族"
        assert b"rag_retrieval_total" not in text, "失败态不得输出伪全量指标"

    def test_disabled_mode_still_returns_empty(self, monkeypatch):
        """未启用（声明性 no-op）仍返回空——不得把「关闭态」误标成「故障」。"""
        from app.core import prometheus as p

        monkeypatch.setattr(p, "_real_metrics", None)
        monkeypatch.setattr(p, "_METRICS_ENABLED", False)
        monkeypatch.setattr(p, "_build_failure", None)
        assert p.get_prometheus_metrics() == b""

    def test_recovery_clears_unavailable_flag(self, monkeypatch):
        """构建恢复成功后，自曝行消失（失败状态可撤销）。"""
        p = self._pin_failure(monkeypatch)
        assert b"docmind_metrics_unavailable 1" in p.get_prometheus_metrics()

        monkeypatch.setattr(p, "_build_metrics", lambda: {"STUB": object()})
        text = p.get_prometheus_metrics()
        assert b"docmind_metrics_unavailable" not in text
