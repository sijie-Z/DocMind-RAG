"""TerminalManager 单测（issue #477 任务 1）——全部基于 FakeBackend，无 Docker。

覆盖：创建/配额（用户/组织）/空闲与最长寿命回收/退出后保留窗口/ring buffer
溢出与 dropped_total 累计/读取截断/单 writer/越权/输入上限/信号/关停。
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.terminal.manager import (
    FakeBackend,
    TerminalBusy,
    TerminalConfig,
    TerminalError,
    TerminalInputTooLarge,
    TerminalManager,
    TerminalNotFound,
    TerminalQuotaExceeded,
)


def make_manager(**config_overrides) -> tuple[TerminalManager, list[FakeBackend]]:
    backends: list[FakeBackend] = []

    def factory(**kwargs) -> FakeBackend:
        backend = FakeBackend(**kwargs)
        backends.append(backend)
        return backend

    config = TerminalConfig(**config_overrides)
    manager = TerminalManager(config=config, backend_factory=factory, worker_id="test-worker")
    return manager, backends


async def wait_status(session, status: str, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while session.status != status:
        if loop.time() > deadline:
            raise AssertionError(f"session 状态 {session.status!r}，期望 {status!r}")
        await asyncio.sleep(0.01)


async def test_create_get_list_and_spawn() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10, session_id="chat-1", cols=100, rows=40)
        assert session.status == "running"
        assert backends[0].spawned is True
        assert backends[0].spawn_args == (100, 40)

        assert manager.get(session.terminal_id, user_id=1) is session
        listed = manager.list_for_user(1)
        assert [s.terminal_id for s in listed] == [session.terminal_id]
        public = session.to_public_dict()
        assert public["terminal_id"] == session.terminal_id
        assert public["session_id"] == "chat-1"
        assert public["status"] == "running"
        assert public["created_at"] and public["last_activity_at"]
    finally:
        await manager.shutdown()


async def test_per_user_quota() -> None:
    manager, _ = make_manager(max_per_user=2)
    try:
        first = await manager.create(user_id=1, org_id=10)
        second = await manager.create(user_id=1, org_id=10)
        with pytest.raises(TerminalQuotaExceeded):
            await manager.create(user_id=1, org_id=10)

        # kill 一个后配额释放。
        assert await manager.kill(first.terminal_id, user_id=1) is True
        third = await manager.create(user_id=1, org_id=10)
        assert third.terminal_id
        assert manager.has_local(second.terminal_id)
    finally:
        await manager.shutdown()


async def test_per_org_quota_counts_across_users() -> None:
    manager, _ = make_manager(max_per_user=5, max_per_org=2)
    try:
        await manager.create(user_id=1, org_id=99)
        await manager.create(user_id=2, org_id=99)
        with pytest.raises(TerminalQuotaExceeded):
            await manager.create(user_id=3, org_id=99)
        # 其它组织不受影响。
        other = await manager.create(user_id=3, org_id=100)
        assert other.terminal_id
    finally:
        await manager.shutdown()


async def test_idle_timeout_reclaims_session() -> None:
    manager, backends = make_manager(idle_timeout_seconds=900, max_lifetime_seconds=3600)
    try:
        session = await manager.create(user_id=1, org_id=10)
        reclaimed = await manager.sweep(now_mono=session.last_activity_mono + 901)
        assert [(s.terminal_id, why) for s, why in reclaimed] == [
            (session.terminal_id, "idle_timeout")
        ]
        assert manager.get_local(session.terminal_id) is None
        assert backends[0].closed is True
        assert session.close_reason == "idle_timeout"
    finally:
        await manager.shutdown()


async def test_max_lifetime_wins_over_activity() -> None:
    manager, _ = make_manager(idle_timeout_seconds=900, max_lifetime_seconds=3600)
    try:
        session = await manager.create(user_id=1, org_id=10)
        # 即便刚刚有活动，超过最长寿命也必须回收。
        # 显式越过边界 1 秒：+3600 时 (created + 3600) - created 可能因浮点舍入
        # 得到 3599.999…，测试会偶发落入 idle_timeout 分支（CI flaky）。
        reclaimed = await manager.sweep(now_mono=session.created_mono + 3601)
        assert [(s.terminal_id, why) for s, why in reclaimed] == [
            (session.terminal_id, "max_lifetime")
        ]
    finally:
        await manager.shutdown()


async def test_exited_session_reclaimed_after_retention() -> None:
    manager, backends = make_manager(exit_retention_seconds=10)
    try:
        session = await manager.create(user_id=1, org_id=10)
        await asyncio.sleep(0)  # 让 reader 任务进入阻塞 read
        backends[0].emit_eof(0)
        await wait_status(session, "exited")
        assert session.exit_code == 0

        # 保留窗口内不回收（可继续读取尾部输出）。
        assert await manager.sweep(now_mono=session.last_activity_mono + 5) == []
        reclaimed = await manager.sweep(now_mono=session.last_activity_mono + 11)
        assert [(s.terminal_id, why) for s, why in reclaimed] == [
            (session.terminal_id, "exited_cleanup")
        ]
    finally:
        await manager.shutdown()


async def test_ring_buffer_overflow_tracks_dropped_total() -> None:
    manager, _ = make_manager(ring_buffer_bytes=1024)
    try:
        session = await manager.create(user_id=1, org_id=10)
        await session.append_output(b"a" * 1500)
        assert session.dropped_total == 476
        data, cursor, dropped = await session.snapshot()
        assert len(data) == 1024
        assert cursor == 1500
        assert dropped == 476

        # ring 溢出后 snapshot_since 会报告被覆盖的字节数。
        new_cursor = 1500
        await session.append_output(b"b" * 1600)
        data, cursor, lost = await session.snapshot_since(new_cursor)
        # total=3100，oldest=2076；游标 1500 到 2076 之间的 576 字节已被覆盖。
        assert lost == 576
        assert len(data) == 1024
        assert cursor == 3100
        # 两轮溢出累计：476 + 1600 = 2076。
        assert session.dropped_total == 2076
    finally:
        await manager.shutdown()


async def test_read_pending_truncates_and_drains() -> None:
    manager, _ = make_manager(ring_buffer_bytes=1024, max_read_bytes=256)
    try:
        session = await manager.create(user_id=1, org_id=10)
        payload = bytes(range(256)) * 12  # 3072 字节，超过 ring 容量
        await session.append_output(payload)

        collected = b""
        first = await manager.read(session.terminal_id, user_id=1)
        assert len(first[0]) == 256
        assert first[1] is True  # 因 max_bytes 截断
        collected += first[0]
        while True:
            data, truncated, dropped = await manager.read(session.terminal_id, user_id=1)
            collected += data
            if not truncated:
                break
        # 环里只剩最后 1024 字节，读取全程无阻塞。
        assert collected == payload[-1024:]
        assert dropped > 0
        assert session.dropped_total == len(payload) - 1024
    finally:
        await manager.shutdown()


async def test_read_timeout_returns_empty_when_no_output() -> None:
    manager, _ = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        data, truncated, dropped = await manager.read(session.terminal_id, user_id=1, timeout=0.05)
        assert data == b""
        assert truncated is False
        assert dropped == 0
    finally:
        await manager.shutdown()


async def test_read_returns_output_when_data_arrives() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        await asyncio.sleep(0)
        backends[0].feed(b"hello")
        data, truncated, _ = await manager.read(session.terminal_id, user_id=1, timeout=1.0)
        assert data == b"hello"
        assert truncated is False
    finally:
        await manager.shutdown()


async def test_single_writer_rejects_concurrent_send() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        backend = backends[0]
        backend.send_delay = 0.05

        async def do_send() -> int:
            return await manager.send(session.terminal_id, user_id=1, data=b"ls\n")

        first = asyncio.create_task(do_send())
        await asyncio.sleep(0)  # 让 first 拿到 writer 锁
        second = asyncio.create_task(do_send())
        results = await asyncio.gather(first, second, return_exceptions=True)

        successes = [r for r in results if isinstance(r, int)]
        busy = [r for r in results if isinstance(r, TerminalBusy)]
        assert len(successes) == 1
        assert len(busy) == 1
        assert len(backend.sent) == 1
    finally:
        await manager.shutdown()


async def test_foreign_user_cannot_access() -> None:
    manager, _ = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        assert manager.get(session.terminal_id, user_id=2) is None
        with pytest.raises(TerminalNotFound):
            await manager.send(session.terminal_id, user_id=2, data=b"id\n")
        with pytest.raises(TerminalNotFound):
            await manager.read(session.terminal_id, user_id=2)
        assert await manager.kill(session.terminal_id, user_id=2) is False
        assert manager.has_local(session.terminal_id)
    finally:
        await manager.shutdown()


async def test_input_size_limit() -> None:
    manager, _ = make_manager(max_input_bytes=16)
    try:
        session = await manager.create(user_id=1, org_id=10)
        with pytest.raises(TerminalInputTooLarge):
            await manager.send(session.terminal_id, user_id=1, data=b"x" * 17)
        assert await manager.send(session.terminal_id, user_id=1, data=b"x" * 16) == 16
    finally:
        await manager.shutdown()


async def test_kill_closes_backend_and_removes_session() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        assert await manager.kill(session.terminal_id, user_id=1) is True
        assert manager.get_local(session.terminal_id) is None
        assert backends[0].closed is True
        assert session.status == "closed"
        # 二次 kill 返回 False（已不存在）。
        assert await manager.kill(session.terminal_id, user_id=1) is False
    finally:
        await manager.shutdown()


async def test_signal_kill_marks_session_exited() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        await asyncio.sleep(0)
        await manager.signal(session.terminal_id, user_id=1, sig="SIGKILL")
        await wait_status(session, "exited")
        assert backends[0].signals == ["KILL"]
        assert session.exit_code == -9
    finally:
        await manager.shutdown()


async def test_resize_forwarded() -> None:
    manager, backends = make_manager()
    try:
        session = await manager.create(user_id=1, org_id=10)
        await manager.resize(session.terminal_id, user_id=1, cols=132, rows=43)
        assert backends[0].resizes == [(132, 43)]
    finally:
        await manager.shutdown()


async def test_resize_bounds_rejected() -> None:
    manager, _ = make_manager(max_cols=1000, max_rows=500)
    try:
        session = await manager.create(user_id=1, org_id=10)
        with pytest.raises(TerminalError):
            await manager.resize(session.terminal_id, user_id=1, cols=0, rows=24)
    finally:
        await manager.shutdown()


async def test_shutdown_reclaims_everything() -> None:
    manager, backends = make_manager()
    await manager.create(user_id=1, org_id=10)
    await manager.create(user_id=2, org_id=10)
    await manager.shutdown()
    assert manager.list_for_user(1) == []
    assert manager.list_for_user(2) == []
    assert all(b.closed for b in backends)


def test_normalize_signal_handles_sig_prefix() -> None:
    """回归：lstrip("SIG") 会把 "INT" 削成 "NT"（字符集合语义）。"""
    from app.agent.terminal.manager import normalize_signal

    assert normalize_signal("INT") == "INT"
    assert normalize_signal("SIGINT") == "INT"
    assert normalize_signal("sigkill") == "KILL"
    assert normalize_signal("TERM") == "TERM"
