# -*- coding: utf-8 -*-
"""Pytest 配置与公共 fixture。"""
import pytest
from fastapi.testclient import TestClient

# 延迟导入，避免未设置环境时加载 app 失败
@pytest.fixture(scope="session")
def client():
    from app.main import app
    return TestClient(app)
