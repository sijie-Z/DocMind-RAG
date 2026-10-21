"""Unit tests for the worker liveness heartbeat and its healthcheck (issue #156)."""

import os
import tempfile
from pathlib import Path

import pytest

import worker_healthcheck
from app.worker import heartbeat


def test_default_heartbeat_path_is_portable():
    # 默认值必须落在平台临时目录下：Windows 无 /tmp，写死路径既行为错误
    # 也触发 bandit B108 阻断门禁（见 issue #156 CI）
    default = Path(heartbeat.DEFAULT_HEARTBEAT_FILE)
    assert default.parent == Path(tempfile.gettempdir())
    assert default.name == "docmind_worker_heartbeat"


def test_age_none_when_heartbeat_missing(tmp_path):
    assert heartbeat.heartbeat_age(str(tmp_path / "missing")) is None


def test_touch_creates_fresh_heartbeat(tmp_path):
    path = tmp_path / "heartbeat"
    heartbeat.touch_heartbeat(str(path))
    age = heartbeat.heartbeat_age(str(path))
    assert age is not None
    assert age < 5.0


def test_touch_refreshes_existing_heartbeat(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("")
    os.utime(path, (1000.0, 1000.0))

    heartbeat.touch_heartbeat(str(path))

    assert path.stat().st_mtime > 1000.0


def test_stale_heartbeat_seen_with_injected_time(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("")
    os.utime(path, (1000.0, 1000.0))

    assert heartbeat.heartbeat_age(str(path), now=1045.0) == pytest.approx(45.0)


def test_check_healthy_when_fresh_and_unhealthy_when_stale(tmp_path):
    path = tmp_path / "heartbeat"
    heartbeat.touch_heartbeat(str(path))
    mtime = path.stat().st_mtime

    code, message = worker_healthcheck.check(path=str(path), max_age=90.0, now=mtime + 10.0)
    assert code == 0
    assert "healthy" in message

    code, message = worker_healthcheck.check(path=str(path), max_age=90.0, now=mtime + 90.1)
    assert code == 1
    assert "stale" in message


def test_check_missing_file_is_unhealthy(tmp_path):
    code, message = worker_healthcheck.check(path=str(tmp_path / "missing"), max_age=90.0, now=0.0)
    assert code == 1
    assert "no heartbeat" in message


def test_env_overrides_are_honored(tmp_path, monkeypatch):
    target = tmp_path / "hb_env"
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(target))
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "5")

    assert heartbeat.heartbeat_path() == str(target)
    assert heartbeat.heartbeat_max_age() == 5.0

    heartbeat.touch_heartbeat()  # 不传 path：应走 env 指定路径
    assert target.exists()


def test_invalid_max_age_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "not-a-number")
    assert heartbeat.heartbeat_max_age() == heartbeat.DEFAULT_MAX_AGE_SECONDS


def test_touch_never_raises_when_path_unusable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # 普通文件占位，使 mkdir 必然失败

    heartbeat.touch_heartbeat(str(blocker / "sub" / "heartbeat"))  # 不应抛异常


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    target = tmp_path / "hb"
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(target))
    monkeypatch.delenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", raising=False)

    assert worker_healthcheck.main() == 1  # 心跳不存在 → unhealthy

    heartbeat.touch_heartbeat()
    assert worker_healthcheck.main() == 0  # 刚刷新 → healthy
    assert "healthy" in capsys.readouterr().out
