"""issue #301（杂项批）判别用例：#24 / #25 / #32（#31 见 tests/api/）。

覆盖三条「假成功/静默吞」家族项：
- #24 `notifications.py`：非缺列数据库故障不得被吞成 200 形态；缺列回退须 warning 留痕；
- #25 `llm_config.py`：坏配置值不得凭空消失——保留 invalid 占位条目 + warning；
- #32 `core/prometheus.py`：指标构建失败须 ERROR 留痕（恰一次）+ /metrics 自曝不可用。
"""
import datetime
import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.api.v1.endpoints.llm_config as llm_config_module
from app.api.v1.endpoints.notifications import get_notifications

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


def _legacy_missing_column_error():
    """旧库缺列形态：MySQL 1054 unknown column（notifications.target_route）。"""
    return ProgrammingError(
        "SELECT notifications.target_route, notifications.target_id FROM notifications",
        {},
        Exception("(1054, \"Unknown column 'notifications.target_route' in 'field list'\")"),
    )


async def _call_list(db):
    return await get_notifications(
        skip=0, limit=20, unread_only=False, type=None, q=None,
        current_user=SimpleNamespace(id=7), db=db,
    )


class TestNotificationsFallbackHonesty:
    """#24：回退查询不得吞任意故障；缺列回退不得零留痕。"""

    @pytest.mark.asyncio
    async def test_db_outage_is_not_masked_as_success(self):
        """连接丢失（非缺列）必须上抛（全局处理 → 500），不再伪装成
        「查询成功、只是没有路由字段」的 200 形态。"""
        exc = OperationalError(
            "SELECT notifications.* FROM notifications",
            {},
            Exception("(2006, 'MySQL server has gone away')"),
        )
        db = _ScriptedSession(exc, [])
        with pytest.raises(OperationalError):
            await _call_list(db)

    @pytest.mark.asyncio
    async def test_legacy_missing_columns_fallback_is_logged(self, caplog):
        """确为旧库缺列 → 回退可用（字段保真为 None），且必须有 warning 留痕。"""
        rows = [(1, "文档已就绪", "c1", "document", False, datetime.datetime(2026, 1, 1))]
        db = _ScriptedSession(
            _legacy_missing_column_error(),
            [_RowResult(scalar=1), _RowResult(scalar=1), _RowResult(rows=rows)],
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
