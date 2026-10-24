"""S6 regression test — _save_session_interaction 必须校验会话归属。

此前该函数按 session_id 直接取会话（没有 user_id 过滤），任何登录用户只要
带上别人的 session_id 就能把消息写进他人会话（读取路径 _load_session_messages
已按 user_id 过滤）。修复后会话查找按 id + user_id 限定；若 id 已属于他人，
拒绝写入而不是占用该 id 新建会话。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import agent as agent_module


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _result(value, scalar=0):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    # issue #476：seq 分配（next_message_seq）在落库前多一次 max 查询。
    r.scalar = MagicMock(return_value=scalar)
    return r


def _make_session(*execute_results):
    session = MagicMock()
    results = list(execute_results)

    async def _execute(*args, **kwargs):
        # issue #499：seq 分配在行锁内读计数器 + MAX 兜底 + UPDATE（多次 execute）。
        # 未显式编排的后续查询按"无计数/无消息"安全默认应答，避免 mock 序列耗尽。
        if results:
            return results.pop(0)
        return _result(None, scalar=0)

    session.execute = AsyncMock(side_effect=_execute)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_write_into_foreign_session_refused(monkeypatch):
    """会话属于其他用户时：拒绝写入，不落任何消息。"""
    session = _make_session(
        _result(None),  # id + user_id 查找：不是调用者的会话
        _result("sess-1"),  # 但该 id 已被他人占用
    )
    monkeypatch.setattr(agent_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(session))

    result = await agent_module._save_session_interaction("sess-1", 1, "q", 1, "answer")

    assert result is None
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_owned_session_gets_messages(monkeypatch):
    """调用者自己的会话：正常写入 user + assistant 两条消息。"""
    owned = MagicMock()
    owned.user_id = 1
    owned.title = "Existing session"
    # T4：落库新增 org 口径——会话归属组织须与本次有效组织一致。
    owned.organization_id = 1
    session = _make_session(_result(owned), _result(None, scalar=0))
    monkeypatch.setattr(agent_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(session))

    result = await agent_module._save_session_interaction("sess-1", 1, "q", 1, "answer")

    assert result is not None
    assert session.add.call_count == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_session_created_for_caller(monkeypatch):
    """会话不存在且 id 未被占用：由调用者创建新会话。"""
    session = _make_session(
        _result(None),
        _result(None),
        _result(None, scalar=0),  # issue #476：seq 分配查询
    )
    monkeypatch.setattr(agent_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(session))

    result = await agent_module._save_session_interaction("sess-new", 1, "q", 1, "answer")

    assert result is not None
    added = [call.args[0] for call in session.add.call_args_list]
    new_session = added[0]
    assert new_session.id == "sess-new"
    assert new_session.user_id == 1
    session.commit.assert_awaited_once()
