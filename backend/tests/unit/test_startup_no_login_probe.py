"""issue #246 回归护栏：启动路径不得对演示账号做真实登录探测。

背景：`main.py` 曾在启动时以固定口令 `guest/123456` 真登录做"自检"。
#233 加固后该口令永不可能正确（密码随机生成 / 由环境变量指定，且已存在账号绝不重置），
于是每次启动都给 `login:fail:guest` +1；5 次后账号锁定，此后 `AccountLockedError`
冒泡打崩启动（实测：`Application startup failed. Exiting.`）。

护栏语义：启动路径只允许"创建/补齐"级操作（`ensure_demo_user`），
**不得出现任何登录尝试**（`authenticate_user`）。任何未来加回的自检都必须不阻塞启动。
"""

import inspect

from app import main


def test_startup_never_probes_demo_login():
    src = inspect.getsource(main)
    assert "authenticate_user" not in src, (
        "启动路径不得尝试登录（#246：账号锁定后 AccountLockedError 会打崩启动）"
    )
