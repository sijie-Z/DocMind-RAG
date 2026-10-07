"""issue #234：bucket 懒创建此前是**死代码**（无任何调用者）——
全新 MinIO（空实例）首次 put 必然 `NoSuchBucket`。修复后由 put/get 入口懒调用。
"""
import io
from unittest.mock import MagicMock

from app.core import minio_client as mc_module
from app.core.config import settings


def _make_client(monkeypatch, *, exists: bool, boom_on_check: bool = False):
    fake = MagicMock()
    if boom_on_check:
        fake.bucket_exists.side_effect = Exception("minio down")
    else:
        fake.bucket_exists.return_value = exists
    monkeypatch.setattr(mc_module, "Minio", lambda *a, **k: fake)
    return mc_module.MinioClient(), fake


def test_put_creates_bucket_when_missing(monkeypatch):
    client, fake = _make_client(monkeypatch, exists=False)

    client.put_object("o1", io.BytesIO(b"x"), 1)

    fake.make_bucket.assert_called_once_with(settings.MINIO_BUCKET_NAME)
    fake.put_object.assert_called_once()


def test_bucket_check_runs_once_per_client(monkeypatch):
    client, fake = _make_client(monkeypatch, exists=True)

    client.put_object("o1", io.BytesIO(b"x"), 1)
    client.put_object("o2", io.BytesIO(b"x"), 1)

    assert fake.bucket_exists.call_count == 1


def test_failed_check_is_nonblocking_and_retried(monkeypatch):
    """检查失败不阻断 put（与既有容错风格一致），且**不置标记** → 下次重试。"""
    client, fake = _make_client(monkeypatch, exists=False, boom_on_check=True)

    client.put_object("o1", io.BytesIO(b"x"), 1)  # 不应抛
    client.put_object("o2", io.BytesIO(b"x"), 1)

    assert fake.bucket_exists.call_count == 2, "失败后应重试而不是永久放弃"


def test_existing_bucket_not_recreated(monkeypatch):
    client, fake = _make_client(monkeypatch, exists=True)

    client.get_object("o1")

    fake.make_bucket.assert_not_called()
