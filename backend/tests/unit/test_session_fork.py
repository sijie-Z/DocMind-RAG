"""issue #476 / #499：会话 fork/seed —— 边界判定、前缀复制、谱系、记忆隔离、checkpoint 克隆。

本文件是 fork 语义的判别性回归网（先红后绿）。核心不变量（issue #499 收口后）：

- **边界 = ``seq <= boundary``（含边界消息）**：USER/SYSTEM/ASSISTANT 边界都复制
  到该消息为止——首个 USER 边界复制 1 条（不再是 copied=0 的歧义）。ASSISTANT
  边界仍须是该轮次最后一条 assistant（中间消息 409）。
- **边界消息 pending → 400**：meta_data 带 tool_status=pending 或 status=pending
  的未结算消息不能作为切点。
- **默认边界 = 最后一条已结算消息**：尾部未答复的 USER 消息不复制，不报错。
- **谱系不可变**：parent/root/fork_point 记录在子会话行上；父会话删除后子会话
  谱系仍可查（ancestors 停止于缺失父节点，parent_missing=true）。
- **上下文隔离**：复制的是消息快照（新 id + 重新编号的 seq），父会话不变；
  记忆命名空间 `session:<child>` 与 `session:<parent>` 完全分离，默认不复制父记忆，
  只有显式 seed 写入子命名空间。子会话 settings.history_limit=200。
- **活跃任务**：源会话存在非终态任务时，必须走 checkpoint 克隆且存在可用快照，
  否则 409（运行中任务不可被消息级热分叉）。
- **checkpoint 克隆**：新 id、绑定子会话、状态 PENDING（issue #499，可 resume）、
  快照深拷贝、run.fork_origin 记录来源（避免伪历史）；待审批 / 执行中工具拒绝。
- **幂等**：body/header 幂等键重复请求返回同一子会话；DB 唯一约束
  (user_id, idempotency_key) 是并发双建的最终防线。

变异点（任一被改坏，对应用例必红）：
1. USER 边界不包含该消息 → USER 边界 copied/seq 断言红。
2. 去掉"assistant 中间消息"检查 → 409 用例红。
3. 默认边界不跳过尾部 USER → 默认用例红。
4. 复制时复用源消息 id / seq 不重排 → 唯一约束/断言红。
5. seed 写进父命名空间或复制父记忆 → 隔离断言红。
6. fork 不校验 org/user → 跨组织用例红（409/成功 vs 404）。
7. 克隆复用源 task id / 不改 session_id / 不深拷 → 克隆断言红。
8. 克隆任务状态写成 PAUSED → PENDING 契约用例红。
9. 去掉 DB 唯一约束 → 同 key 并发用例建出两个子会话（必红）。
"""

import asyncio
import copy
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.memory_bridge import AgentMemoryBridge
from app.core.database import Base
from app.exceptions import NotFoundError, ValidationError
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.services import chat_seq, memory_service, session_fork_service
from app.services.session_fork_service import (
    ForkBoundaryInvalidError,
    ForkCheckpointRequiredError,
    ForkCheckpointUnavailableError,
    ForkIdempotencyConflictError,
    ForkQuotaExceededError,
    fork_session,
    get_lineage,
)

USER_ID = 1
ORG_ID = 1


# ── Fixtures / helpers ──────────────────────────────────────────────────────


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fork.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离全局记忆缓存与 Redis（seed 只落进程内记忆，测试对网络封闭）。"""
    monkeypatch.setattr(memory_service, "memory_systems", {})
    monkeypatch.setattr(
        memory_service.AgentMemorySystem, "_get_redis", AsyncMock(return_value=None)
    )


async def _mk_session(
    db,
    sid: str,
    *,
    user_id: int = USER_ID,
    org_id: int | None = ORG_ID,
    title: str = "母会话",
    settings: dict | None = None,
    parent_session_id: str | None = None,
    root_session_id: str | None = None,
    fork_point_message_id: str | None = None,
    fork_reason: str | None = None,
) -> ChatSession:
    session = ChatSession(
        id=sid,
        user_id=user_id,
        organization_id=org_id,
        title=title,
        status=ChatSessionStatus.ACTIVE,
        settings=settings,
        parent_session_id=parent_session_id,
        root_session_id=root_session_id or sid,
        fork_point_message_id=fork_point_message_id,
        fork_reason=fork_reason,
    )
    db.add(session)
    await db.flush()
    return session


async def _mk_msg(
    db,
    sid: str,
    seq: int,
    mtype: MessageType,
    content: str,
    meta: dict | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=sid,
        seq=seq,
        content=content,
        message_type=mtype,
        meta_data=meta,
    )
    db.add(msg)
    await db.flush()
    return msg


async def _messages(db, sid: str) -> list[ChatMessage]:
    rows = await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.seq.asc())
    )
    return list(rows.scalars().all())


async def _fork(db, source_id: str, **kwargs):
    return await fork_session(
        db,
        source_id=source_id,
        user_id=kwargs.pop("user_id", USER_ID),
        organization_id=kwargs.pop("organization_id", ORG_ID),
        **kwargs,
    )


class _FakeRedis:
    """最小 Redis：支持 SET NX EX / GET / DELETE（fork 幂等路径所需的最小面）。"""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


# ── 前缀复制 / seq 重排 / 母会话不受影响 ────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_copies_prefix_renumbers_seq_and_keeps_parent_intact(db_factory):
    """在 A1 处分叉：只复制 [U1, A1]，子会话 seq 重排为 1..N、消息 id 全换新，
    附件元数据保留；母会话消息与计数原样不动。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1", title="原标题")
        u1 = await _mk_msg(db, src.id, 1, MessageType.USER, "问题一", {"file_ids": ["doc-1"]})
        a1 = await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        u2 = await _mk_msg(db, src.id, 3, MessageType.USER, "问题二")
        a2 = await _mk_msg(db, src.id, 4, MessageType.ASSISTANT, "回答二")
        await db.commit()
        parent_ids_before = [m.id for m in await _messages(db, src.id)]
        parent_created_before = [m.created_at for m in await _messages(db, src.id)]

        result = await _fork(db, src.id, message_id=a1.id)
        child = result.session

        child_msgs = await _messages(db, child.id)
        assert [m.seq for m in child_msgs] == [1, 2], "子会话 seq 必须从 1 重新编号"
        assert [m.content for m in child_msgs] == ["问题一", "回答一"]
        assert [m.message_type for m in child_msgs] == [MessageType.USER, MessageType.ASSISTANT]
        assert child_msgs[0].id != u1.id and child_msgs[1].id != a1.id, "复制必须使用新消息 id"
        assert child_msgs[0].meta_data == {"file_ids": ["doc-1"]}, "附件/引用元数据必须保留"

        # 谱系/元数据
        assert child.parent_session_id == src.id
        assert child.root_session_id == src.id
        assert child.fork_point_message_id == a1.id
        assert child.fork_reason == "user_fork"
        assert child.title == "原标题 (fork)"
        assert child.message_count == 2
        assert child.settings["fork"]["copied_message_count"] == 2
        assert child.settings["fork"]["checkpoint_cloned"] is False
        assert child.settings["history_limit"] == 200, "issue #499：fork 子会话 history_limit=200"
        assert child.next_message_seq == 2, "复制后计数器随复制条数推进"
        assert result.copied_message_count == 2
        assert result.boundary_seq == 2
        assert result.fork_point_message_id == a1.id
        assert result.idempotent is False

        # 母会话完全不受影响（id / seq / 内容 / 行数逐一不变）
        parent_msgs = await _messages(db, src.id)
        assert [m.id for m in parent_msgs] == parent_ids_before
        assert [u1.id, a1.id, u2.id, a2.id] == parent_ids_before
        assert len(parent_msgs) == 4
        assert [m.seq for m in parent_msgs] == [1, 2, 3, 4]
        assert [m.created_at for m in parent_msgs] == parent_created_before
        assert parent_msgs[0].content == "问题一"


@pytest.mark.asyncio
async def test_fork_default_boundary_skips_open_trailing_user_turn(db_factory):
    """默认边界 = 最后一条已结算消息：尾部未答复的 USER 不复制、不报错。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await _mk_msg(db, src.id, 3, MessageType.USER, "尚未答复的问题")
        await db.commit()

        result = await _fork(db, src.id)

        assert result.copied_message_count == 2
        assert result.session.fork_point_message_id is not None
        child_msgs = await _messages(db, result.session.id)
        assert [m.content for m in child_msgs] == ["问题一", "回答一"], (
            "尾部未答复的 USER 不得进入子会话（开放轮次被排除在边界之外）"
        )


@pytest.mark.asyncio
async def test_fork_empty_source_yields_empty_child(db_factory):
    """空会话默认边界为空前缀：允许分叉出空子会话（前缀平凡满足已结算）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await db.commit()

        result = await _fork(db, src.id)

        assert result.copied_message_count == 0
        assert result.fork_point_message_id is None
        assert await _messages(db, result.session.id) == []
        assert result.session.root_session_id == src.id
        assert result.session.message_count == 0


# ── 开放轮次 / 边界合法性（409） ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_rejects_intermediate_assistant_message(db_factory):
    """同一轮内 assistant 后面还有 assistant：前者是"中间消息"，fork 必须 409。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        first = await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "中间回答")
        final = await _mk_msg(db, src.id, 3, MessageType.ASSISTANT, "最终回答")
        await db.commit()

        with pytest.raises(ForkBoundaryInvalidError) as excinfo:
            await _fork(db, src.id, message_id=first.id)
        assert excinfo.value.status_code == 409
        assert excinfo.value.error_code == "FORK_BOUNDARY_INVALID"

        # 最后一条 assistant（轮次终态）才是合法边界
        result = await _fork(db, src.id, message_id=final.id)
        assert result.copied_message_count == 3


@pytest.mark.asyncio
async def test_fork_user_boundary_includes_boundary_message(db_factory):
    """issue #499：USER 边界 = seq <= boundary（含该 USER），不再是"之前"分叉。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        u2 = await _mk_msg(db, src.id, 3, MessageType.USER, "问题二")
        await _mk_msg(db, src.id, 4, MessageType.ASSISTANT, "回答二")
        await db.commit()

        result = await _fork(db, src.id, message_id=u2.id)
        assert result.copied_message_count == 3
        assert result.boundary_seq == 3
        assert result.fork_point_message_id == u2.id, "fork_point 就是边界消息本身"
        assert [m.content for m in await _messages(db, result.session.id)] == [
            "问题一",
            "回答一",
            "问题二",
        ]


@pytest.mark.asyncio
async def test_fork_at_first_user_message_copies_one_message(db_factory):
    """issue #499：第一条消息就是 USER → 复制 1 条（消除 copied=0 的歧义）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        u1 = await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        result = await _fork(db, src.id, message_id=u1.id)
        assert result.copied_message_count == 1
        assert result.boundary_seq == 1
        assert result.fork_point_message_id == u1.id
        child_msgs = await _messages(db, result.session.id)
        assert [m.content for m in child_msgs] == ["问题一"]


@pytest.mark.asyncio
async def test_fork_user_boundary_after_open_turn_is_allowed(db_factory):
    """issue #499：边界只看 seq，不再要求前缀以 ASSISTANT 结尾（连续 USER 也可切）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await _mk_msg(db, src.id, 3, MessageType.USER, "未答复")
        u4 = await _mk_msg(db, src.id, 4, MessageType.USER, "又一个未答复")
        await db.commit()

        result = await _fork(db, src.id, message_id=u4.id)
        assert result.copied_message_count == 4
        assert result.boundary_seq == 4
        assert [m.content for m in await _messages(db, result.session.id)] == [
            "问题一",
            "回答一",
            "未答复",
            "又一个未答复",
        ]


@pytest.mark.asyncio
async def test_fork_allows_system_tail_before_user_boundary(db_factory):
    """SYSTEM 是带外数据：USER 边界（含该 USER）可跨越前面的 SYSTEM 前缀。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await _mk_msg(db, src.id, 3, MessageType.SYSTEM, "系统提示")
        u2 = await _mk_msg(db, src.id, 4, MessageType.USER, "问题二")
        await db.commit()

        result = await _fork(db, src.id, message_id=u2.id)
        assert result.copied_message_count == 4
        assert result.boundary_seq == 4
        copied = await _messages(db, result.session.id)
        assert [m.message_type for m in copied] == [
            MessageType.USER,
            MessageType.ASSISTANT,
            MessageType.SYSTEM,
            MessageType.USER,
        ]


@pytest.mark.asyncio
async def test_fork_allows_system_message_boundary(db_factory):
    """issue #499：SYSTEM 消息本身可以作为边界（复制到该消息为止）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        system = await _mk_msg(db, src.id, 3, MessageType.SYSTEM, "系统提示")
        await db.commit()

        result = await _fork(db, src.id, message_id=system.id)
        assert result.copied_message_count == 3
        assert result.boundary_seq == 3
        assert result.fork_point_message_id == system.id
        assert [m.content for m in await _messages(db, result.session.id)] == [
            "问题一",
            "回答一",
            "系统提示",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"tool_status": "pending"}, {"status": "pending"}])
async def test_fork_rejects_pending_boundary_message(db_factory, meta):
    """issue #499：边界消息 meta_data 标 pending（未结算占位）→ 400 VALIDATION_ERROR。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        pending = await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "正在生成……", meta)
        await db.commit()

        with pytest.raises(ValidationError) as excinfo:
            await _fork(db, src.id, message_id=pending.id)
        assert excinfo.value.status_code == 400
        assert excinfo.value.error_code == "VALIDATION_ERROR"
        assert excinfo.value.detail["message_id"] == pending.id
        # 不得产生任何子会话
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src.id)))
            .scalars()
            .all()
        )
        assert list(children) == []


@pytest.mark.asyncio
async def test_fork_boundary_message_must_belong_to_source(db_factory):
    """边界消息不存在 / 属于其他会话 → 404（不泄露归属）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        other = await _mk_session(db, "other-1")
        foreign = await _mk_msg(db, other.id, 1, MessageType.ASSISTANT, "别人的回答")
        await db.commit()

        with pytest.raises(NotFoundError):
            await _fork(db, src.id, message_id="does-not-exist")
        with pytest.raises(NotFoundError):
            await _fork(db, src.id, message_id=foreign.id)


# ── 活跃任务 / checkpoint 克隆 ─────────────────────────────────────────────


def _envelope(source_id: str, *, pending: dict | None = None) -> dict:
    """最小可用 v1 快照信封（parse_snapshot 只要求 ctx 为 dict）。"""
    return {
        "v": checkpoint.CHECKPOINT_VERSION,
        "session_id": source_id,
        "ctx": {"task_id": "src-task-1", "query": "原问题"},
        "plan": None,
        "history": [],
        "config": {},
        "run": {"pending_approval": pending} if pending else {"replan_attempts": 0},
        "partial_output": "",
        "updated_by": "after_planning",
    }


@pytest.mark.asyncio
async def test_fork_running_task_requires_checkpoint(db_factory):
    """源会话有运行中任务：不带 include_checkpoint → 409；带但没有快照 → 409。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="task-active",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="进行中的问题",
                status=TaskStatus.RUNNING,
            )
        )
        await db.commit()

        with pytest.raises(ForkCheckpointRequiredError) as excinfo:
            await _fork(db, src.id)
        assert excinfo.value.status_code == 409
        assert excinfo.value.error_code == "FORK_CHECKPOINT_REQUIRED"

        with pytest.raises(ForkCheckpointUnavailableError) as excinfo2:
            await _fork(db, src.id, include_checkpoint=True)
        assert excinfo2.value.status_code == 409
        assert excinfo2.value.error_code == "FORK_CHECKPOINT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_fork_clones_settled_checkpoint_as_pending_task(db_factory, monkeypatch):
    """克隆最近已结算 checkpoint：新 id、绑定子会话、PENDING、深拷贝、来源可查、可 resume。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        a1 = await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="src-task-1",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="原问题",
                status=TaskStatus.COMPLETED,
                context_snapshot=_envelope(src.id),
                plan_id="plan-1",
                completed_steps=1,
                total_steps=2,
                progress=0.5,
            )
        )
        await db.commit()

        result = await _fork(db, src.id, message_id=a1.id, include_checkpoint=True)
        child = result.session
        assert result.checkpoint_task_id is not None

        clone = (
            await db.execute(select(AgentTask).where(AgentTask.session_id == child.id))
        ).scalar_one()
        assert clone.id == result.checkpoint_task_id
        assert clone.id != "src-task-1", "克隆必须使用新任务 id"
        assert clone.status == TaskStatus.PENDING, (
            "issue #499：克隆任务必须是 pending 态（可直接 load_for_resume 续跑）"
        )
        assert clone.user_id == USER_ID and clone.organization_id == ORG_ID
        assert clone.query == "原问题"
        assert clone.plan_id == "plan-1" and clone.progress == 0.5
        snap = clone.context_snapshot
        assert snap["session_id"] == child.id, "快照信封必须绑定子会话"
        assert snap["run"]["fork_origin"]["source_task_id"] == "src-task-1"
        assert snap["run"]["fork_origin"]["source_session_id"] == src.id
        assert snap["ctx"]["query"] == "原问题"
        assert snap["ctx"]["task_id"] == clone.id, (
            "快照 ctx.task_id 必须指向克隆任务（replay 隔离）"
        )

        # 落库后的篡改不得穿透母任务（深拷贝是这一层的行为保证）
        tampered = dict(snap)
        tampered["ctx"] = {**snap["ctx"], "query": "被篡改"}
        clone.context_snapshot = tampered
        await db.commit()
        async with db_factory() as db3:
            source_task = await db3.get(AgentTask, "src-task-1")
            assert source_task.context_snapshot["ctx"]["query"] == "原问题", (
                "checkpoint 克隆必须是深拷贝，子会话修改不得污染母任务快照"
            )

        # 可 resume：PENDING → RUNNING 是合法恢复边
        monkeypatch.setattr(checkpoint, "AsyncSessionLocal", db_factory)
        resumed = await checkpoint.begin_resume(clone.id)
        assert resumed.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_fork_rejects_checkpoint_with_pending_approval(db_factory):
    """最近 checkpoint 有 pending_approval（待审批/执行中工具）→ 409，绝不克隆。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="task-approval",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="待审批任务",
                status=TaskStatus.WAITING_APPROVAL,
                context_snapshot=_envelope(src.id, pending={"tool_name": "execute_python"}),
            )
        )
        await db.commit()

        with pytest.raises(ForkCheckpointUnavailableError):
            await _fork(db, src.id, include_checkpoint=True)

        # 不得产生任何子会话/克隆任务
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src.id)))
            .scalars()
            .all()
        )
        assert list(children) == []


@pytest.mark.asyncio
async def test_clone_snapshot_deep_copies_parsed_envelope(db_factory, monkeypatch):
    """_clone_snapshot 必须深拷解析后的信封。

    变异点：把 deepcopy 换成 dict(...) 浅拷贝 → `snapshot["run"]` 与输入共享，
    写入 fork_origin 会就地污染被解析的信封（sentinel 断言红）。
    """
    sentinel = _envelope("src-1")
    sentinel["run"] = {"note": "keep-me"}
    pristine = copy.deepcopy(sentinel)

    async def _fake_raw(_db, _task_id):
        return sentinel

    monkeypatch.setattr(session_fork_service, "_load_snapshot_raw", _fake_raw)
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="src-task-1",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="原问题",
                status=TaskStatus.COMPLETED,
                context_snapshot=_envelope(src.id),
            )
        )
        await db.commit()

        result = await _fork(db, src.id, include_checkpoint=True)
        clone = (
            await db.execute(select(AgentTask).where(AgentTask.session_id == result.session.id))
        ).scalar_one()

        assert sentinel == pristine, "克隆不得就地改写被解析的信封（浅拷贝会污染 run）"
        assert clone.context_snapshot["ctx"] is not sentinel["ctx"]
        assert clone.context_snapshot["run"] is not sentinel["run"]
        assert clone.context_snapshot["run"]["fork_origin"]["source_task_id"] == "src-task-1"


@pytest.mark.asyncio
async def test_seed_write_failure_is_marked_honestly(db_factory, monkeypatch):
    """seed 写入失败：fork 仍成功，但 is_seeded 必须下调并落库（不谎报）。"""
    monkeypatch.setattr(session_fork_service, "_write_seed_memory", AsyncMock(return_value=False))
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        result = await _fork(db, src.id, seed="父会话摘要")

        assert result.is_seeded is False
        assert result.session.settings["fork"]["is_seeded"] is False
        assert result.session.settings["fork"]["seed_write_failed"] is True
        # 子会话本身照常落库（seed 是尽力而为，不阻塞 fork）
        child = await db.get(ChatSession, result.session.id)
        assert child is not None
        assert child.message_count == 2


@pytest.mark.asyncio
async def test_fork_rejects_checkpoint_with_pending_plan_approval(db_factory):
    """计划审批残留在快照里（任务已取消收敛）同样拒绝克隆。

    变异点：只查 run.pending_approval 不查 run.pending_plan_approval →
    克隆出带过期审批门的 PAUSED 任务，resume 会重挂已取消的计划审批。
    """
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        envelope = _envelope(src.id)
        envelope["run"]["pending_plan_approval"] = {"plan_id": "p1", "steps": []}
        db.add(
            AgentTask(
                id="task-plan-approval",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="待计划审批任务",
                status=TaskStatus.CANCELLED,
                context_snapshot=envelope,
            )
        )
        await db.commit()

        with pytest.raises(ForkCheckpointUnavailableError):
            await _fork(db, src.id, include_checkpoint=True)


@pytest.mark.asyncio
async def test_poisoned_snapshot_does_not_block_plain_fork(db_factory):
    """损坏的快照列（#278/R1 毒丸）不得把"是否有运行中任务"判定炸成 500。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="poison-done",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="已完成但快照损坏",
                status=TaskStatus.COMPLETED,
                context_snapshot=None,
            )
        )
        await db.commit()
        # 绕过 ORM 写半截 JSON（生产 MySQL 的 JSON 列校验拦得住，sqlite 拦不住，
        # 但 ORM 读路径同样会 json.loads —— 毒丸防线必须两端都在）。
        await db.execute(
            text("UPDATE agent_tasks SET context_snapshot = :raw WHERE id = 'poison-done'"),
            {"raw": '{"v": 1, "ctx": {'},
        )
        await db.commit()

        result = await _fork(db, src.id)
        assert result.copied_message_count == 2, "坏快照行不得阻塞普通 fork"


@pytest.mark.asyncio
async def test_poisoned_snapshot_is_skipped_for_checkpoint_clone(db_factory):
    """checkpoint 克隆候选里的损坏快照必须被跳过（409），不得 500。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="poison-running",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="运行中但快照损坏",
                status=TaskStatus.RUNNING,
                context_snapshot=None,
            )
        )
        await db.commit()
        await db.execute(
            text("UPDATE agent_tasks SET context_snapshot = :raw WHERE id = 'poison-running'"),
            {"raw": '{"v": 1, "ctx": {'},
        )
        await db.commit()

        with pytest.raises(ForkCheckpointUnavailableError):
            await _fork(db, src.id, include_checkpoint=True)


@pytest.mark.asyncio
async def test_terminal_task_does_not_block_plain_fork(db_factory):
    """终态任务不是"运行中"：不带 include_checkpoint 的正常 fork 不受影响。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="task-done",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="已完成",
                status=TaskStatus.COMPLETED,
                context_snapshot=_envelope(src.id),
            )
        )
        await db.commit()

        result = await _fork(db, src.id)
        assert result.copied_message_count == 2
        assert result.checkpoint_task_id is None


@pytest.mark.asyncio
async def test_paused_clone_task_does_not_block_plain_fork(db_factory):
    """PAUSED（fork 克隆产物）不是运行中任务：子会话仍可继续分叉。

    变异点：把 PAUSED 加进 _ACTIVE_TASK_STATUSES → 本用例红（子会话永远无法再分叉）。
    """
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="task-paused",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="分叉快照",
                status=TaskStatus.PAUSED,
                context_snapshot=None,
            )
        )
        await db.commit()

        result = await _fork(db, src.id)
        assert result.copied_message_count == 2
        assert result.checkpoint_task_id is None


# ── 记忆隔离 / seed ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_memory_isolated_and_seed_lands_in_child_only(db_factory):
    """默认不复制母记忆；显式 seed 只写入子命名空间 session:<child>。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        parent_bridge = AgentMemoryBridge(
            session_id=src.id, organization_id=ORG_ID, user_id=USER_ID
        )
        await parent_bridge.record_insight("母会话私有记忆")

        result = await _fork(db, src.id, seed="父会话摘要：讨论了第一季预算")
        child = result.session
        assert result.is_seeded is True
        assert child.settings["fork"]["is_seeded"] is True
        assert child.settings["fork"]["seed_length"] == len("父会话摘要：讨论了第一季预算")

        child_bridge = AgentMemoryBridge(
            session_id=child.id, organization_id=ORG_ID, user_id=USER_ID
        )
        child_insights = [i["content"] for i in child_bridge.system.reflective.insights]
        parent_insights = [i["content"] for i in parent_bridge.system.reflective.insights]
        assert child_insights == ["父会话摘要：讨论了第一季预算"], "seed 必须写入子命名空间"
        assert "母会话私有记忆" not in child_insights, "默认不得复制母会话记忆"
        assert parent_insights == ["母会话私有记忆"], "子会话写入不得污染母会话记忆"
        assert child_bridge.system is not parent_bridge.system, "父子必须使用不同记忆系统实例"


@pytest.mark.asyncio
async def test_fork_without_seed_copies_no_memory(db_factory):
    """不带 seed：子命名空间为空（与母记忆完全隔离）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        parent_bridge = AgentMemoryBridge(
            session_id=src.id, organization_id=ORG_ID, user_id=USER_ID
        )
        await parent_bridge.record_insight("母会话记忆")

        result = await _fork(db, src.id)
        assert result.is_seeded is False
        child_bridge = AgentMemoryBridge(
            session_id=result.session.id, organization_id=ORG_ID, user_id=USER_ID
        )
        assert child_bridge.system.reflective.insights == []


# ── 幂等 / 配额 / 租户隔离 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_idempotency_key_replays_same_child(db_factory):
    """同一 Idempotency-Key 重复请求返回同一子会话，不重复复制。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        first = await _fork(db, src.id, idempotency_key="idem-abc")
        first_snapshot = [(m.id, m.seq, m.content) for m in await _messages(db, first.session.id)]
        second = await _fork(db, src.id, idempotency_key="idem-abc")
        third = await _fork(db, src.id, idempotency_key="idem-other")

        assert first.session.id == second.session.id
        assert second.idempotent is True and first.idempotent is False
        assert third.session.id != first.session.id
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src.id)))
            .scalars()
            .all()
        )
        assert len(children) == 2, "同 key 不得产生第二个子会话"
        assert [
            (m.id, m.seq, m.content) for m in await _messages(db, first.session.id)
        ] == first_snapshot, "重放不得重新复制消息（id/seq 必须原样）"


@pytest.mark.asyncio
async def test_idempotency_key_persisted_on_child_column(db_factory):
    """issue #499：幂等键落在 chat_sessions.idempotency_key（DB 唯一约束的事实列）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        first = await _fork(db, src.id, idempotency_key="idem-column")
        child = await db.get(ChatSession, first.session.id)
        assert child.idempotency_key == "idem-column"
        assert child.settings["fork"]["idempotency_key"], "settings 仍保留 sha256 审计指纹"
        assert child.settings["fork"]["idempotency_key"] != "idem-column"


@pytest.mark.asyncio
async def test_fork_idempotency_db_unique_conflict_replays_single_child(db_factory, monkeypatch):
    """DB 唯一约束 (user_id, idempotency_key) 是并发双建的最终防线。

    模拟"快路径检查同时错过（并发窗口）"：第二次请求的预检查被强制 miss，
    插入必然撞唯一约束；服务必须回滚并读回先到者（200 重放），且只存在一个子会话。

    变异试验：去掉模型/迁移里的唯一约束 → 第二次插入成功 → 断言红。
    """
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()
        first = await _fork(db, src.id, idempotency_key="dup-key")

    real_find = session_fork_service._find_idempotent_child
    state = {"calls": 0}

    async def _miss_first_check(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(session_fork_service, "_find_idempotent_child", _miss_first_check)

    async with db_factory() as db:
        second = await _fork(db, src.id, idempotency_key="dup-key")
        assert second.idempotent is True
        assert second.session.id == first.session.id
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src.id)))
            .scalars()
            .all()
        )
        assert len(children) == 1, "并发窗口下同一幂等键只能建出一个子会话"


@pytest.mark.asyncio
async def test_fork_concurrent_same_key_creates_single_child(db_factory, monkeypatch):
    """asyncio 并发两个同 key 请求：一个创建、另一个（或 409/重放），总数必须为 1。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

    real_find = session_fork_service._find_idempotent_child
    state = {"misses": 0}

    async def _racy_find(*args, **kwargs):
        # 让两个并发请求的"预检查"都 miss，强制它们进入插入/唯一约束竞争窗口。
        if state["misses"] < 2:
            state["misses"] += 1
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(session_fork_service, "_find_idempotent_child", _racy_find)
    # Redis 不可用（测试默认）：并发闸门退化为 DB 唯一约束。

    async def _run_one():
        async with db_factory() as db:
            return await _fork(db, src.id, idempotency_key="race-key")

    results = await asyncio.gather(_run_one(), _run_one(), return_exceptions=True)
    successes = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]
    assert 1 <= len(successes) <= 2, f"并发结果异常: {results!r}"
    assert all(isinstance(e, session_fork_service.ForkInProgressError) for e in errors), (
        f"只允许可重试的 FORK_IN_PROGRESS，实际: {errors!r}"
    )
    async with db_factory() as db:
        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src.id)))
            .scalars()
            .all()
        )
        assert len(children) == 1, "同一幂等键并发只允许一个子会话"
    if len(successes) == 2:
        assert successes[0].session.id == successes[1].session.id


@pytest.mark.asyncio
async def test_fork_in_progress_holder_returns_409_after_short_wait(db_factory, monkeypatch):
    """Redis NX 已被占但 DB 尚无子会话：短暂轮询后 409 FORK_IN_PROGRESS（可重试）。"""
    fake = _FakeRedis()
    monkeypatch.setattr(session_fork_service, "redis_client", fake)
    monkeypatch.setattr(session_fork_service, "IDEMPOTENCY_WAIT_SECONDS", 0.15)
    monkeypatch.setattr(session_fork_service, "IDEMPOTENCY_POLL_INTERVAL_SECONDS", 0.02)

    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        src_id = src.id
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()
        # 另一个请求已抢占但尚未落库。
        fake.data[f"fork:idem:{USER_ID}:inflight"] = "ghost-child-id"

        with pytest.raises(session_fork_service.ForkInProgressError) as excinfo:
            await _fork(db, src.id, idempotency_key="inflight")
        assert excinfo.value.status_code == 409
        assert excinfo.value.error_code == "FORK_IN_PROGRESS"
        assert excinfo.value.detail["holder_session_id"] == "ghost-child-id"
        assert f"fork:idem:{USER_ID}:inflight" not in fake.data, (
            "超时后必须释放观察到的抢占值，避免崩溃请求把幂等键锁死 24h"
        )

        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src_id)))
            .scalars()
            .all()
        )
        assert list(children) == []


@pytest.mark.asyncio
async def test_fork_records_child_id_in_redis_idempotency_key(db_factory, monkeypatch):
    """创建成功后 Redis 键值为子 session_id（重复请求据此快路径去重）。"""
    fake = _FakeRedis()
    monkeypatch.setattr(session_fork_service, "redis_client", fake)
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        result = await _fork(db, src.id, idempotency_key="redis-ok")
        assert fake.data[f"fork:idem:{USER_ID}:redis-ok"] == result.session.id
        assert result.idempotent is False


@pytest.mark.asyncio
async def test_fork_releases_idempotency_claim_when_boundary_invalid(db_factory, monkeypatch):
    """抢占后 404/409 失败必须释放幂等键，否则同键重试会被占用值挡住。"""
    fake = _FakeRedis()
    monkeypatch.setattr(session_fork_service, "redis_client", fake)
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        with pytest.raises(NotFoundError):
            await _fork(db, src.id, message_id="missing", idempotency_key="release-me")
        assert f"fork:idem:{USER_ID}:release-me" not in fake.data, "失败请求不得留下幂等键"

        # 同一键重试可以正常创建。
        result = await _fork(db, src.id, idempotency_key="release-me")
        assert result.idempotent is False


@pytest.mark.asyncio
async def test_fork_idempotency_key_used_in_other_org_is_409_not_500(db_factory):
    """唯一键被其他组织行占用：不泄露/不回放其数据，明确 409（不是 500）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1", org_id=ORG_ID)
        src_id = src.id
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            ChatSession(
                id="other-org-child",
                user_id=USER_ID,
                organization_id=ORG_ID + 1,
                title="别的组织",
                status=ChatSessionStatus.ACTIVE,
                root_session_id="other-org-source",
                parent_session_id="other-org-source",
                idempotency_key="cross-org-key",
            )
        )
        await db.commit()

        with pytest.raises(ForkIdempotencyConflictError) as excinfo:
            await _fork(db, src.id, idempotency_key="cross-org-key")
        assert excinfo.value.status_code == 409
        assert excinfo.value.error_code == "FORK_IDEMPOTENCY_CONFLICT"

        children = (
            (await db.execute(select(ChatSession).where(ChatSession.parent_session_id == src_id)))
            .scalars()
            .all()
        )
        assert list(children) == [], "不得在调用者组织内建出第二个子会话"


@pytest.mark.asyncio
async def test_next_message_seq_persists_counter_and_reconciles_direct_inserts(db_factory):
    """issue #499：seq 分配推进 chat_sessions.next_message_seq，并与直插消息的 MAX 对账。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await db.commit()

        assert await chat_seq.next_message_seq(db, src.id) == 1
        await db.commit()
        row = await db.get(ChatSession, src.id)
        assert row.next_message_seq == 1

        assert await chat_seq.next_message_seq(db, src.id) == 2
        await db.commit()
        row = await db.get(ChatSession, src.id)
        assert row.next_message_seq == 2

        # 带外直插（不经 chat_seq）后，下一次分配必须越过 MAX(seq)，不得重复。
        await _mk_msg(db, src.id, 10, MessageType.USER, "直插")
        await db.commit()
        assert await chat_seq.next_message_seq(db, src.id) == 11
        await db.commit()
        row = await db.get(ChatSession, src.id)
        assert row.next_message_seq == 11


@pytest.mark.asyncio
async def test_idempotent_replay_returns_cloned_task_not_source_task(db_factory):
    """带 checkpoint 的幂等重放必须返回**克隆任务** id，绝不返回母任务 id。

    变异点：settings.fork.checkpoint_task_id 误存母任务 id → 重放响应让客户端
    resume 母 run，把收尾答案写回母会话（伪历史）。
    """
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        db.add(
            AgentTask(
                id="src-task-1",
                user_id=USER_ID,
                organization_id=ORG_ID,
                session_id=src.id,
                query="原问题",
                status=TaskStatus.COMPLETED,
                context_snapshot=_envelope(src.id),
            )
        )
        await db.commit()

        first = await _fork(db, src.id, include_checkpoint=True, idempotency_key="idem-ckpt")
        second = await _fork(db, src.id, include_checkpoint=True, idempotency_key="idem-ckpt")

        assert first.checkpoint_task_id is not None
        assert first.checkpoint_task_id != "src-task-1"
        assert second.session.id == first.session.id
        assert second.idempotent is True
        assert second.checkpoint_task_id == first.checkpoint_task_id, (
            "重放必须返回同一克隆任务 id（否则客户端会去 resume 母 run）"
        )
        fork_meta = second.session.settings["fork"]
        assert fork_meta["checkpoint_task_id"] == first.checkpoint_task_id
        assert fork_meta["checkpoint_source_task_id"] == "src-task-1"


@pytest.mark.asyncio
async def test_fork_quota_exceeded_rejects_with_400_and_detail(db_factory, monkeypatch):
    """复制消息数超过上限 → 400 VALIDATION_ERROR，detail 带 messages/limit。"""
    monkeypatch.setattr(session_fork_service, "FORK_MAX_COPIED_MESSAGES", 2)
    async with db_factory() as db:
        src = await _mk_session(db, "src-1")
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await _mk_msg(db, src.id, 3, MessageType.USER, "问题二")
        await _mk_msg(db, src.id, 4, MessageType.ASSISTANT, "回答二")
        await db.commit()

        with pytest.raises(ForkQuotaExceededError) as excinfo:
            await _fork(db, src.id)
        assert excinfo.value.status_code == 400
        assert excinfo.value.error_code == "VALIDATION_ERROR"
        assert excinfo.value.detail == {"messages": 4, "limit": 2}


@pytest.mark.asyncio
async def test_fork_cross_org_and_foreign_user_are_404(db_factory):
    """跨组织（同用户）与跨用户一律 404，绝不 200/403（避免枚举）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-1", user_id=USER_ID, org_id=ORG_ID)
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        with pytest.raises(NotFoundError):
            await _fork(db, src.id, organization_id=ORG_ID + 1)
        with pytest.raises(NotFoundError):
            await _fork(db, src.id, user_id=USER_ID + 1)
        with pytest.raises(NotFoundError):
            await get_lineage(db, session_id=src.id, user_id=USER_ID + 1, organization_id=ORG_ID)
        with pytest.raises(NotFoundError):
            await get_lineage(db, session_id=src.id, user_id=USER_ID, organization_id=ORG_ID + 1)


@pytest.mark.asyncio
async def test_fork_allows_legacy_session_without_org(db_factory):
    """历史会话 organization_id 为 NULL 时，本人仍可分叉（子会话继承调用者的组织）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-legacy", org_id=None)
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        result = await _fork(db, src.id, organization_id=ORG_ID)
        assert result.session.organization_id == ORG_ID


@pytest.mark.asyncio
async def test_seed_without_organization_is_honestly_skipped(db_factory):
    """无组织上下文无法建立记忆命名空间：seed 必须如实标记未生效（不谎报）。"""
    async with db_factory() as db:
        src = await _mk_session(db, "src-legacy", org_id=None)
        await _mk_msg(db, src.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, src.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        result = await _fork(db, src.id, organization_id=None, seed="父会话摘要")
        assert result.session.organization_id is None
        assert result.is_seeded is False, "无组织时 seed 不可能写入记忆，不得声称已 seed"
        assert result.session.settings["fork"]["is_seeded"] is False
        assert result.session.settings["fork"]["seed_skipped_reason"] == "no_organization"
        assert result.session.settings["fork"]["seed_length"] == len("父会话摘要")


# ── 迁移 016（SQLite 冒烟：真 MySQL 不可用时的静态验证补充）─────────────────


def test_migration_016_backfills_seq_and_enforces_idempotency_uniqueness(tmp_path):
    """迁移 016：回填 next_message_seq=MAX(seq)，幂等键 (user_id,key) 唯一，NULL 不参与。

    走 SQLite 单测替代真 MySQL（本机 Docker/MySQL 未启动）；MySQL 的 UNIQUE 对
    NULL 语义与 SQLite 一致，均允许多个 NULL。
    """
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    migration_path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/016_fork_seq_idempotency.py"
    )
    spec = importlib.util.spec_from_file_location("mig016_fork_seq_idempotency", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(f"sqlite:///{tmp_path / 'mig016.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE chat_sessions (id VARCHAR(36) PRIMARY KEY, user_id INTEGER NOT NULL)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE chat_messages (id VARCHAR(36) PRIMARY KEY, "
            "session_id VARCHAR(36), seq INTEGER)"
        )
        conn.exec_driver_sql("INSERT INTO chat_sessions (id,user_id) VALUES ('s1',1),('s2',1)")
        conn.exec_driver_sql(
            "INSERT INTO chat_messages (id,session_id,seq) VALUES ('m1','s1',1),('m2','s1',7)"
        )
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

        rows = conn.exec_driver_sql(
            "SELECT id, next_message_seq FROM chat_sessions ORDER BY id"
        ).fetchall()
        assert rows == [("s1", 7), ("s2", 0)], "必须按 MAX(chat_messages.seq) 回填"

        # 同一用户 + 同一幂等键 → 唯一约束拒绝；NULL 可重复（存量行不参与）。
        conn.exec_driver_sql("UPDATE chat_sessions SET idempotency_key='k' WHERE id='s1'")
        try:
            conn.exec_driver_sql("UPDATE chat_sessions SET idempotency_key='k' WHERE id='s2'")
        except IntegrityError as exc:
            assert "UNIQUE constraint failed" in str(exc)
        else:
            raise AssertionError("唯一约束缺失：(user_id, idempotency_key) 未生效")

        migration.downgrade()
    engine.dispose()


# ── 谱系 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lineage_walks_ancestors_and_children(db_factory):
    """三级谱系：根 → 子 → 孙。祖先由根到父、children 为直接子级、depth 正确。"""
    async with db_factory() as db:
        root = await _mk_session(db, "root-1", title="根")
        await _mk_msg(db, root.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, root.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()

        child_result = await _fork(db, root.id)
        child = child_result.session
        child_msgs = await _messages(db, child.id)
        grand_result = await _fork(db, child.id, message_id=child_msgs[-1].id)
        grand = grand_result.session

        lineage = await get_lineage(
            db, session_id=grand.id, user_id=USER_ID, organization_id=ORG_ID
        )
        assert lineage["session_id"] == grand.id
        assert lineage["root_session_id"] == root.id
        assert lineage["parent_session_id"] == child.id
        assert lineage["is_fork"] is True
        assert lineage["depth"] == 2
        assert [a["id"] for a in lineage["ancestors"]] == [root.id, child.id]
        assert lineage["children"] == []

        root_lineage = await get_lineage(
            db, session_id=root.id, user_id=USER_ID, organization_id=ORG_ID
        )
        assert root_lineage["is_fork"] is False
        assert root_lineage["depth"] == 0
        assert root_lineage["ancestors"] == []
        assert [c["id"] for c in root_lineage["children"]] == [child.id]

        child_lineage = await get_lineage(
            db, session_id=child.id, user_id=USER_ID, organization_id=ORG_ID
        )
        assert [c["id"] for c in child_lineage["children"]] == [grand.id]


@pytest.mark.asyncio
async def test_lineage_survives_parent_deletion(db_factory):
    """删除父会话后：子会话仍在、谱系字段可查（祖先链止于缺失节点并如实标记）。"""
    async with db_factory() as db:
        root = await _mk_session(db, "root-1")
        await _mk_msg(db, root.id, 1, MessageType.USER, "问题一")
        await _mk_msg(db, root.id, 2, MessageType.ASSISTANT, "回答一")
        await db.commit()
        child = (await _fork(db, root.id)).session

        await db.execute(delete(ChatMessage).where(ChatMessage.session_id == root.id))
        await db.delete(root)
        await db.commit()

        lineage = await get_lineage(
            db, session_id=child.id, user_id=USER_ID, organization_id=ORG_ID
        )
        assert lineage["parent_session_id"] == root.id
        assert lineage["root_session_id"] == root.id
        assert lineage["ancestors"] == []
        assert lineage["parent_missing"] is True, "父节点已删除必须如实标记，而非假装是根"
        assert lineage["is_fork"] is True
        # 子会话自身与其消息不受影响
        assert len(await _messages(db, child.id)) == 2
