"""issue #301（杂项批）API 面判别用例：#31 /files 故障分流；#25 损坏条目可见性。

- #31：MinIO 存储故障（InternalError / 连不上）必须如实 503 + 日志；只有
  NoSuchKey 才是 404「文件不存在」——此前一切故障都被伪装成 404。
- #25：Redis 里的坏配置值在 GET /llm-config 仍可见（invalid 标记），
  total 不减、原始值不出网。
"""
import json
import logging
from types import SimpleNamespace

from minio.error import S3Error

from app.core.minio_client import minio_client
from app.core.security import get_current_user


class _FakeMinioBackend:
    """只实现 /files 读取路径所需方法；get_object 一律抛注入的异常。"""

    def __init__(self, exc):
        self._exc = exc

    def bucket_exists(self, name):
        return True

    def get_object(self, bucket, obj):
        raise self._exc


def _s3_error(code: str) -> S3Error:
    return S3Error(None, code, f"test-{code}", "/docmind/avatars/1.png", "req-1", "host-1")


def _pin_minio_failure(monkeypatch, exc):
    monkeypatch.setattr(minio_client, "_client", _FakeMinioBackend(exc))


class TestFilesProxyStorageErrors:
    """/files/{path}：对象不存在（404）与存储故障（503）必须分流。"""

    def test_missing_object_still_404(self, client, monkeypatch):
        _pin_minio_failure(monkeypatch, _s3_error("NoSuchKey"))
        resp = client.get("/files/avatars/1.png")
        assert resp.status_code == 404, "对象确实不存在 → 维持 404"

    def test_internal_error_is_503_not_404(self, client, monkeypatch, caplog):
        _pin_minio_failure(monkeypatch, _s3_error("InternalError"))
        with caplog.at_level(logging.ERROR, logger="app.main"):
            resp = client.get("/files/avatars/1.png")
        assert resp.status_code == 503, "存储故障必须如实 503，不得伪装 404"
        assert resp.json()["code"] == 503
        assert any(
            r.name == "app.main" and r.levelno >= logging.ERROR for r in caplog.records
        ), "503 分流必须留 error 日志（原先零留痕）"

    def test_unreachable_storage_is_503_not_404(self, client, monkeypatch):
        _pin_minio_failure(monkeypatch, ConnectionError("MinIO unreachable"))
        resp = client.get("/files/avatars/1.png")
        assert resp.status_code == 503


class _FakeConfigRedis:
    def __init__(self, mapping):
        self._mapping = mapping

    async def hgetall(self, key):
        return self._mapping


_BAD_RAW = '{"provider": "deepseek", "api_key": "sk-SECRET-halfwritten'


class TestLlmConfigCorruptVisibility:
    """#25：坏配置值在列表 API 上仍可见（invalid 标记），且原始值不出网。"""

    def test_corrupt_entry_listed_with_marker(self, client, monkeypatch, caplog):
        import app.api.v1.endpoints.llm_config as llm_config_module

        good = json.dumps({
            "id": "good00000001", "provider": "deepseek",
            "config_name": "正常的", "api_key": "sk-abcdefghijklmnop",
        })
        monkeypatch.setattr(
            llm_config_module, "redis_client",
            _FakeConfigRedis({"good00000001": good, "bad000000002": _BAD_RAW}),
        )
        user = SimpleNamespace(id=1, is_superuser=False, role="user")

        async def _override_user():
            return user

        app_ = client.app
        app_.dependency_overrides[get_current_user] = _override_user
        try:
            with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.llm_config"):
                resp = client.get("/api/v1/llm-config")
        finally:
            app_.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2, "坏值条目不得凭空消失（原 bug：total 少 1）"
        invalid = [c for c in body["data"] if c.get("invalid") is True]
        assert len(invalid) == 1
        assert invalid[0]["id"] == "bad000000002"
        assert invalid[0]["invalid_reason"]
        # 原始坏值（可能含半写密钥）绝不出网；好条目密钥仍脱敏
        assert "sk-SECRET" not in resp.text
        assert "_raw_value" not in resp.text
        assert "sk-abcdefghijklmnop" not in resp.text
        assert any(
            r.name == "app.api.v1.endpoints.llm_config" and r.levelno >= logging.WARNING
            for r in caplog.records
        ), "坏值必须 warning 留痕"
