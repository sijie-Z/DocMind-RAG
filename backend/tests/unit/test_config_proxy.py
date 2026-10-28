"""惰性 settings 代理（issue #479 §5.2）。"""

from __future__ import annotations

import pytest

import app.core.config as config_pkg
from app.core.config.loader import reset_settings


def test_import_does_not_load_and_first_access_does():
    reset_settings()
    assert config_pkg.settings._target is None, "import/reset 后代理不得持有 Settings"
    assert config_pkg.settings.APP_NAME  # 首次属性访问触发加载
    assert config_pkg.settings._target is not None


def test_reset_settings_reloads_on_next_access():
    reset_settings()
    assert config_pkg.settings.APP_NAME
    first = config_pkg.settings._target
    assert first is not None
    reset_settings()
    assert config_pkg.settings._target is None
    assert config_pkg.settings.APP_NAME
    assert config_pkg.settings._target is not None


def test_monkeypatch_setattr_roundtrip_on_proxy():
    """现有测试依赖 ``monkeypatch.setattr(settings, "X", v)`` 的写透传。"""
    before = config_pkg.settings.DEBUG
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(config_pkg.settings, "DEBUG", not before)
        assert config_pkg.settings.DEBUG is (not before)
        # 写到的是真实 Settings 对象，而不是代理自身
        assert config_pkg.settings._target.DEBUG is (not before)
    finally:
        mp.undo()
    assert config_pkg.settings.DEBUG is before


def test_dict_forwards_to_target_for_pytest92_snapshot():
    """pytest 9.2 从 ``target.__dict__`` 快照旧值；代理必须暴露真实字段。

    回归背景：pytest 9.2（#10644）对无数据描述符的非类目标改用
    ``target.__dict__`` 记录旧值，缺省则 undo 走 ``delattr``。代理若只暴露
    ``_target``，undo 会把 Settings 字段真正删掉，后续读取全部
    ``AttributeError``（CI 962 条级联失败）。
    """
    reset_settings()
    assert config_pkg.settings.APP_NAME
    target = config_pkg.settings._target
    assert target is not None
    assert config_pkg.settings.__dict__ is target.__dict__
    assert config_pkg.settings.__dict__["APP_NAME"] == target.APP_NAME


def test_pytest92_style_undo_restores_field():
    """快照到的旧值必须能用 ``setattr`` 写回（而非 delattr 删字段）。"""
    reset_settings()
    settings = config_pkg.settings
    original = settings.APP_NAME
    snapshot = settings.__dict__.get("APP_NAME", object())
    assert snapshot == original

    settings.APP_NAME = original + "-patched"
    assert settings.__dict__["APP_NAME"] == original + "-patched"

    settings.APP_NAME = snapshot
    assert original == settings.APP_NAME
    assert "APP_NAME" in settings.__dict__


def test_resolve_embedding_model_follows_proxy(monkeypatch):
    from app.core.config import resolve_embedding_model

    monkeypatch.setattr(config_pkg.settings, "ENABLE_LOCAL_EMBEDDING", True)
    monkeypatch.setattr(config_pkg.settings, "LOCAL_EMBEDDING_MODEL", "nomic-local")
    monkeypatch.setattr(config_pkg.settings, "EMBEDDING_MODEL", "remote-model")
    assert resolve_embedding_model() == "nomic-local"

    monkeypatch.setattr(config_pkg.settings, "ENABLE_LOCAL_EMBEDDING", False)
    assert resolve_embedding_model() == "remote-model"
