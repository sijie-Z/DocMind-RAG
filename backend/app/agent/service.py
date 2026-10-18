"""Agent service — high-level interface for the PER agent system.

Wires together the PER agent loop, tools, memory, and context engine
into a single service for the API layer.

Supports:
- Agent chat with full PER flow (SSE streaming)
- Hybrid RAG + Agent mode (retrieve first, then agent)
- Session persistence (config + plan + memory state)
"""

import asyncio
import functools
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop
from app.agent.memory_bridge import AgentMemoryBridge
from app.core.database import AsyncSessionLocal
from app.core.log_safety import log_safe
from app.models.agent_task import AgentTask, TaskStatus
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

# Force import of all tool modules to trigger registration
import app.agent.core_tools  # noqa: F401 — core tools (14)
import app.agent.tools  # noqa: F401 — tools package (19)

# ── 引用文档（体验第四波 @引用）────────────────────────────────────────────

MAX_REFERENCED_DOCS = 10
MAX_EXCERPT_CHARS = 700


async def build_referenced_document_context(
    document_ids: list[str],
    query: str,
    organization_id: int,
    pipeline=None,
) -> tuple[str | None, list[str]]:
    """把用户 @ 引用的知识库文档解析为注入 planner 与 executor 的上下文段。

    取文两段式（第四波实机缺陷修复）：
    ① query 相关分块检索（相关摘要优先）；
    ② 零命中 → 按 document_id 直读文档头部 chunk 兜底（"总结这份文档"类
       元问题与正文零词汇重叠，不能押注在检索命中上）。
    诚实语义：每个引用要么给出正文摘要进上下文，要么给出「为什么用不了」的
    明确警告文案上 SSE —— 绝不静默丢弃（直读成功不算降级，不外发警告）。

    ⚠️ 安全注记（评审 F3，必须有记录）：被引文档的检索分块是**不可信输入**
    ——文档内容可能包含针对模型的指令（prompt injection 载体）。本实现的
    注入姿态与既有 RAG 一致（`context_docs` 直拼检索全文 text，见 loop 合成消息），
    非新类攻击面；现有约束：用户显式引用（非自动召回）+ 组织域校验（跨组织
    拒注入）+ 条数帽（10）× 长度帽（700 字符/条）。**尚无**定界 spotlight /
    指令隔离等主动防护——该方向（引用段包裹与注入过滤）作为后续独立演进项，
    不在本波范围。若未来把引用摘要扩展到自动召回/跨用户语料，须先补防护。

    Returns:
        (document_context, warnings)：document_context 为 None 表示没有
        任何可用引用（不外发空段）；warnings 为逐条中文提示（后端无 i18n
        层，与既有错误文案约定一致：中文文案 + 前端映射）。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.document import Document

    warnings: list[str] = []
    entries: list[dict[str, str]] = []

    ids = [str(d).strip() for d in document_ids if str(d).strip()]
    if len(ids) > MAX_REFERENCED_DOCS:
        for extra in ids[MAX_REFERENCED_DOCS:]:
            warnings.append(f"引用文档超出单次上限（{MAX_REFERENCED_DOCS} 个），已忽略：{extra}")
        ids = ids[:MAX_REFERENCED_DOCS]

    for did in ids:
        doc = None
        try:
            async with AsyncSessionLocal() as db:
                doc = (
                    (await db.execute(select(Document).where(Document.id == did)))
                    .scalars()
                    .first()
                )
        except Exception as e:
            logger.warning("引用文档查询失败 %s: %s", did, log_safe(e))
            warnings.append(f"引用文档不可用（查询失败），已忽略：{did}")
            continue
        if doc is None:
            warnings.append(f"引用文档不可用（不存在），已忽略：{did}")
            continue
        if doc.organization_id != organization_id:
            warnings.append(f"引用文档无权访问（不属于当前组织），已忽略：{did}")
            continue

        title = doc.title or doc.filename or did
        excerpt = ""
        retrieval_note: str | None = None
        if pipeline is not None:
            try:
                results, _ = await pipeline.search_knowledge_base(
                    query, organization_id, top_k=3, document_ids=[did],
                )
                excerpt = "\n".join(
                    (r.get("text") or r.get("snippet") or "").strip()
                    for r in results[:3]
                    if (r.get("text") or r.get("snippet"))
                ).strip()
            except Exception as e:
                logger.warning("引用文档检索失败 %s: %s", did, log_safe(e))
                retrieval_note = f"引用文档「{title}」内容检索失败"
        else:
            retrieval_note = f"引用文档「{title}」摘要服务不可用"

        if not excerpt:
            # 直读兜底（第四波实机缺陷修复）：用户显式引用了该文档——正文该按
            # document_id 直取，不该押注在 query 检索命中上（"总结这份文档"类
            # 元问题与正文零词汇重叠，质量门会全数滤掉 → 修前只注入标题，实机
            # "只找到文档列表信息，没有拿到可总结的正文"）。按 chunk_index 正序
            # 取文档头部若干分块（与 summarize_document 工具同法）；内容仍是不
            # 可信输入（见上方安全注记），长度帽照旧。
            try:
                from app.core.elasticsearch import ElasticsearchTools

                es_res = await ElasticsearchTools.search_documents({
                    "size": 3,
                    "query": {"bool": {"must": [
                        {"term": {"document_id": did}},
                        {"term": {"organization_id": str(organization_id)}},
                    ]}},
                    "sort": [{"chunk_index": {"order": "asc"}}],
                    "_source": ["content"],
                })
                excerpt = "\n".join(
                    (h.get("_source", {}).get("content") or "").strip()
                    for h in es_res.get("hits", {}).get("hits", [])
                ).strip()
            except Exception as e:
                logger.warning("引用文档直读失败 %s: %s", did, log_safe(e))

        if not excerpt:
            excerpt = (doc.description or "").strip()
            # 两条检索路径（query 检索 + 直读）都没拿到正文才降级外发警告；
            # 直读成功不算降级（不打扰用户）。
            warnings.append(
                (retrieval_note + "，仅按标题/描述注入")
                if retrieval_note
                # 检索正常但零命中：如实告知降级为标题/描述
                else f"引用文档「{title}」未检索到相关正文，仅按标题/描述注入"
            )
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS] + "…"
        entries.append({"id": did, "title": title, "excerpt": excerpt})

    if not entries:
        return None, warnings

    lines = ["## 用户显式引用的知识库文档（回答时优先依据这些文档）"]
    for i, e in enumerate(entries, 1):
        lines.append(f"### [{i}] {e['title']}（document_id: {e['id']}）")
        if e["excerpt"]:
            lines.append(e["excerpt"])
    return "\n".join(lines), warnings



async def notify_task_terminal(
    task_id: str, state: TaskStatus, *, error: str | None = None
) -> None:
    """任务终态站内通知（best-effort；由各终态收敛点在前置 mark_terminal
    成功落库后触发）。

    - 覆盖三种终态：completed / failed / cancelled——任务在后台跑完而人已离开
      页面时，铃铛与通知中心仍能收到，点击一键跳回（target_route="Agent" +
      target_id=task_id；前端既有跳转链按 `?id=` 导航到 /agent 并打开回放）。
    - **诚实降级**：通知是旁路，不是主链——任何失败（DB 抖动、WS 不可达等）
      只记 warning 留痕，绝不上抛、绝不回滚/影响任务状态（调用方此刻已落终态）。
    - 标题带 task_id：create_notification 按 title+user 1 分钟去重，同一分钟
      内多个任务收敛时标题必须可区分，否则后来的通知会被吞。
    - 自带 DB 会话（照 checkpoint.save_checkpoint 模式）：本函数被 SSE 收尾
      与异常路径调用，不能假设请求会话的生命周期还健在。
    """
    try:
        row = await checkpoint.load_task_row(task_id)
        if row is None:
            return
        query_part = " ".join((row.query or "").split())[:60]
        if state is TaskStatus.COMPLETED:
            title = f"Agent 任务已完成（{task_id}）"
            content = f"「{query_part}」执行完成，点击查看回放。"
        elif state is TaskStatus.CANCELLED:
            title = f"Agent 任务已取消（{task_id}）"
            content = f"「{query_part}」已取消。"
        else:
            title = f"Agent 任务失败（{task_id}）"
            reason = " ".join((error or "").split())[:100]
            content = f"「{query_part}」执行失败" + (f"：{reason}" if reason else "") + "。"
        async with AsyncSessionLocal() as db:
            await create_notification(
                db,
                user_id=row.user_id,
                title=title,
                content=content,
                type="agent",
                target_route="Agent",
                target_id=str(task_id),
            )
    except Exception as e:
        logger.warning(
            "终态通知发送失败（不影响任务状态）(task=%s, state=%s): %s",
            log_safe(task_id),
            getattr(state, "value", state),
            log_safe(e),
        )


class AgentService:
    """High-level agent interface with session management."""

    def __init__(self, openai_client: AsyncOpenAI | None = None):
        self._client = openai_client

    @property
    def client(self) -> AsyncOpenAI | None:
        if self._client is None:
            from app.dependencies import get_rag_pipeline
            pipeline = get_rag_pipeline()
            self._client = pipeline.openai_client
        return self._client

    @staticmethod
    def _wire_cancel(agent: PERAgentLoop, task_id: str | None) -> None:
        """HITL：给 loop 接上 task 级取消旗查询（无 task_id = 无取消通道，如实不接）。"""
        if task_id:
            agent.cancel_check = functools.partial(checkpoint.is_cancel_requested, task_id)

    async def chat(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        session_id: str | None = None,
        config: AgentConfig | None = None,
        referenced_document_ids: list[str] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the PER agent loop and yield events.

        This is the main entry point for the agent chat API.
        All SSE event streaming goes through this method.

        Args:
            query: User's question or request.
            history: Previous conversation messages.
            organization_id: Multi-tenant organization scope.
            user_id: Current user ID.
            session_id: Optional session ID for config persistence.
            config: Per-request agent configuration overrides.
            referenced_document_ids: 用户显式 @ 引用的知识库文档 id（体验
                第四波）——解析为关键摘要注入 planner 上下文；失效/无权限
                逐条以 warning 事件诚实外发，绝不静默。
        """
        if not self.client:
            yield AgentEvent(type="error", content="LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。")
            return

        # Load or create config
        agent_config = config or AgentConfig()

        if session_id:
            saved_config = await AgentConfig.load_from_redis(session_id)
            if saved_config and not config:
                agent_config = saved_config

        # Apply session-scoped agent_id
        if session_id:
            agent_config.agent_id = f"session:{session_id}"

        # 任务行（issue #201 最小版）：一次 run 一行，checkpoint 的快照载体。
        # 建行失败不影响聊天（fail-open：整个 checkpoint 面静默缺席）。
        task_id: str | None = None
        if agent_config.enable_checkpoint:
            task_id = await checkpoint.create_task(
                user_id=user_id,
                organization_id=organization_id,
                query=query,
            )

        # T7（断流收敛盲窗）：try 从任务行创建后立即开启——覆盖 @引用解析
        # （秒级 await）、引用 warning 与工具元信息两处 run 前 yield、以及
        # agent.run 循环。修复前 try 从 run 才开启：断连/取消落在这些
        # await / yield 上会直接逃逸（无任何 handler），任务行永停 RUNNING。
        agent: PERAgentLoop | None = None
        try:
            # 引用文档（体验第四波 @引用）：解析为 planner 上下文段 + 诚实警告。
            # 解析失败不阻塞聊天——引用是增强不是前提（fail-open 与 checkpoint 同）。
            document_context: str | None = None
            if referenced_document_ids:
                try:
                    from app.dependencies import get_rag_pipeline

                    rag_pipeline = get_rag_pipeline()
                except Exception as e:
                    logger.warning("引用文档摘要服务获取失败: %s", log_safe(e))
                    rag_pipeline = None
                document_context, ref_warnings = await build_referenced_document_context(
                    referenced_document_ids,
                    query,
                    organization_id,
                    pipeline=rag_pipeline,
                )
                for w in ref_warnings:
                    warn_event = AgentEvent(type="warning", content=w)
                    if task_id:
                        warn_event.task_id = task_id
                    yield warn_event

            # Create the PER loop
            agent = PERAgentLoop(
                openai_client=self.client,
                config=agent_config,
                organization_id=organization_id,
                user_id=user_id,
            )
            # HITL 取消：task 级取消旗接线（POST /tasks/{id}/cancel 置旗，run 在
            # 步间/工具前检查点观察并收敛为 run_cancelled → CANCELLED）。
            self._wire_cancel(agent, task_id)

            # Emit available tool count in a metadata event
            tools = agent._get_tools()
            tool_count = len(tools) if tools else 0
            meta_event = AgentEvent(
                type="thinking",
                content=f"可用工具: {tool_count} 个",
                thinking_type="reasoning",
            )
            if task_id:
                meta_event.task_id = task_id
            yield meta_event

            # Run the PER loop（task_id 接通 checkpoint：T1–T4 触发点全部生效；
            # document_context：@引用文档摘要并入预规划上下文）
            async for event in agent.run(
                query, history=history, task_id=task_id, session_id=session_id,
                document_context=document_context,
            ):
                if task_id:
                    event.task_id = task_id
                yield event
        except GeneratorExit:
            # 断流（客户端 abort / 生成器被关闭）：尽力落一次快照 + 收敛为
            # FAILED（可恢复的中断态）——修前 status 永停 RUNNING，产品面
            # 无续跑入口，实机表现为"进行中、无回放"（第四波修复）。
            # T7：pre-run 段（@引用解析 await / warning 与 meta yield）同样
            # 纳入此语义——此时 agent 可能尚未构造：快照无内容可落，
            # 状态收敛照常执行（任务行不得悬挂）。
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except asyncio.CancelledError:
            # 请求被取消（uvicorn 断连取消任务）：同断流语义（快照 + 收敛）。
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except BaseException as exc:
            # run 内异常：快照 + 置 FAILED（FAILED→RUNNING 是既有重试边，可 resume）。
            await self._checkpoint_error(task_id, agent, exc)
            raise
        else:
            if task_id:
                # issue #301 批 A #10/#11：终态写入必须被确认——返回值不再被
                # 丢弃（写失败必有可观测信号），run 级失败（无工具模式 LLM
                # 全败）按标志如实置 FAILED 而不是 COMPLETED。
                # HITL：审批暂停绝不置终态（状态已在暂停点置 waiting_approval，
                # 人类应答经 approval 端点续跑）；协作式取消收敛为 CANCELLED。
                # B：计划审批暂停（waiting_plan_approval）同语义——等人类决策。
                if getattr(agent, "paused_for_approval", False) or getattr(
                    agent, "paused_for_plan_approval", False
                ):
                    pass
                elif getattr(agent, "run_cancelled", False):
                    cancel_event = await self._finalize_task_cancelled(task_id)
                    if cancel_event is not None:
                        cancel_event.task_id = task_id
                        yield cancel_event
                else:
                    finalize_event = await self._finalize_task_terminal(
                        task_id,
                        failed_run=bool(getattr(agent, "run_failed", False)),
                        failure_text=getattr(agent, "run_failure", None),
                    )
                    if finalize_event is not None:
                        finalize_event.task_id = task_id
                        yield finalize_event

        # Persist config for session recovery
        if session_id:
            await agent_config.save_to_redis(session_id)

    async def prepare_resume(
        self,
        task_id: str,
        user_id: int,
        *,
        organization_id: int | None = None,
    ) -> tuple[AgentTask, dict[str, Any] | None]:
        """resume 的同步预检 + 认领（端点层在开流前调用——404/409 必须用 HTTP
        状态码表达，SSE 一旦开始就没有状态码可改了）：

        - 404：任务不存在 / 非本人 / 组织不一致（TaskNotFoundError）；
        - 409：已终态（TaskNotResumableError，detail 回带当前 state）/ 并发 resume
          正持有租约（TaskBusyError）/ **B：正在等待计划审批
          （TaskWaitingPlanApprovalError——直连 resume 会让未获批准的计划被执行，
          必须经 plan-approval 端点应答）**；
        - 成功 = 已拿租约并把状态推进到 RUNNING（retry_count += 1），返回 (task, payload)。
        """
        task, payload = await checkpoint.load_for_resume(
            task_id=task_id, user_id=user_id, organization_id=organization_id,
        )
        if task.is_terminal:
            raise checkpoint.TaskNotResumableError(
                f"任务已处于终态 {task.status.value}，不可恢复",
                detail={"task_id": task_id, "state": task.status.value},
            )
        if task.status == TaskStatus.WAITING_PLAN_APPROVAL:
            # B：审批门不许绕过——resume 的语义是"续跑已批准的计划"，而此处
            # 计划尚未获批；loop 侧另有重挂起兜底（深度防御，两道闸都开着）。
            raise checkpoint.TaskWaitingPlanApprovalError(
                "任务正在等待计划审批，请先经 plan-approval 应答（批准/编辑/拒绝）",
                detail={"task_id": task_id, "state": task.status.value},
            )
        # 认领：Redis 租约 + RUNNING→FAILED→RUNNING（陈旧 RUNNING 接管）等状态流转。
        task = await checkpoint.begin_resume(task_id)
        return task, payload

    async def resume_chat(
        self,
        task_id: str,
        user_id: int,
        *,
        config: AgentConfig | None = None,
        claim: bool = True,
    ) -> AsyncGenerator[AgentEvent, None]:
        """从 checkpoint 恢复一个未终态任务（issue #201 最小版）。

        语义（设计报告 2.3）：
        - 已完成步骤绝不重跑（executor 只挑 pending）；崩溃时在途的步骤重跑一次；
        - 快照里的 config 是 run 起点的规则快照，resume 不重读用户最新配置；
        - 快照缺失/损坏安全降级：从规划阶段干净重跑（没有任何步骤被越过）。
        - 并发/重复 resume 由 Redis 租约 + 状态机拒绝（TaskBusy/NotResumable，
          AppError 子类 → 端点层映射 404/409）。

        claim=False：调用方已先走 prepare_resume（端点预检拿 HTTP 状态码）；
        这里只补读快照，不再重复认领。
        """
        if not self.client:
            yield AgentEvent(type="error", content="LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。")
            return

        if claim:
            task, payload = await self.prepare_resume(task_id, user_id)
        else:
            task, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=user_id)

        if payload:
            query = (payload.get("ctx") or {}).get("query") or task.query
            agent_config = AgentConfig.from_dict(payload.get("config") or {})
            session_id = payload.get("session_id") or None
        else:
            # 快照缺失/损坏：降级为从规划阶段重跑（仍幂等）；规则取调用方传入的配置。
            logger.warning("任务 %s 无可用快照，resume 从规划阶段重新开始", log_safe(task_id))
            query = task.query
            agent_config = config or AgentConfig()
            session_id = None

        agent = PERAgentLoop(
            openai_client=self.client,
            config=agent_config,
            # 安全加固（issue #278/m3，#168/#268 禁模式）：如实传任务归属组织，
            # 绝不 `or 1` 兜底伪造——无组织如实为 None（下游按各自既有的
            # fail-closed 语义处理，与 #257/#268 家族口径一致）。
            organization_id=task.organization_id,
            user_id=task.user_id,
        )
        # HITL：取消旗接线（resume 流同样受协作式取消约束）。
        self._wire_cancel(agent, task_id)
        try:
            async for event in agent.run(
                query, task_id=task_id, resume=payload, session_id=session_id,
            ):
                event.task_id = task_id
                yield event
        except GeneratorExit:
            await self._checkpoint_disconnect(task_id, agent, release_lease=True)
            raise
        except asyncio.CancelledError:
            await self._checkpoint_disconnect(task_id, agent, release_lease=True)
            raise
        except BaseException as exc:
            await self._checkpoint_error(task_id, agent, exc)
            raise
        else:
            # issue #301 批 A #10/#11：同 chat 路径——消费 mark_terminal 返回值，
            # 写失败必显形、run 级失败如实置 FAILED。
            # HITL：resume 流还可能再次暂停（多审批循环）/被取消——语义同 chat。
            # B：计划审批暂停（再次停在计划卡）同语义。
            if getattr(agent, "paused_for_approval", False) or getattr(
                agent, "paused_for_plan_approval", False
            ):
                pass
            elif getattr(agent, "run_cancelled", False):
                cancel_event = await self._finalize_task_cancelled(task_id)
                if cancel_event is not None:
                    cancel_event.task_id = task_id
                    yield cancel_event
            else:
                finalize_event = await self._finalize_task_terminal(
                    task_id,
                    failed_run=bool(getattr(agent, "run_failed", False)),
                    failure_text=getattr(agent, "run_failure", None),
                )
                if finalize_event is not None:
                    finalize_event.task_id = task_id
                    yield finalize_event

    # ── Checkpoint 兜底（断流 / 异常；二者都不得改变既有语义）──────────────

    async def _checkpoint_disconnect(
        self,
        task_id: str | None,
        agent: PERAgentLoop | None,
        *,
        release_lease: bool = False,
    ) -> None:
        """断流兜底（best-effort）：存一份"到此为止"的快照 + 把状态从"正在跑"系
        收敛为 FAILED（可恢复的中断态）——不动等待审批类挂起态。

        agent 为 None（T7：断流落在 run 之前的 @引用解析 await / warning
        yield 上，loop 尚未构造）：没有活状态可落快照，仅执行状态收敛——
        任务行已存在，绝不能因"还没有 agent"而永停 RUNNING。

        快照语义 = 与"进程被硬杀"一致（resume 从最近成功的 checkpoint 继续）。
        状态收敛（第四波实机缺陷修复，见 checkpoint.converge_disconnected）：
        此前断流只落快照、行永停 RUNNING——产品面无 resume 入口、也无看门狗，
        实测任务列表永远"进行中、无回放"（id=0a7e5e8ea580 现场）。FAILED 带着
        既有"重试边"（FAILED→RUNNING），恢复语义不牺牲；绝不写 COMPLETED。
        await 在被关闭/取消的路径上可能被底层打断——允许它失败：最坏退化 =
        恢复点停在上一个 T2 + 状态停 RUNNING（与修前一致），不影响正确性
        （设计报告 E1）。

        release_lease（issue #278/m2，评审 R3）：resume 断流时把本次认领的租约
        归还——断流 = 持有者已终止（生成器正在关闭，不可能再写入），重试应当
        立即可认领；此前快照保存会把租约续到 900s，用户立即重试被 TaskBusy
        拒至多 15 分钟。chat 从未认领租约，保持不释放（避免误删并发 resume
        持有的锁）。

        #300/F1（评审 R9 仪器化差分 trace）：save、converge 与 release 必须是
        **同一个**受保护单元（一个协程里按序执行）。shield 只保护"已进入的
        任务"——两者各自 shield 时（修复前），ASGI 取消路径上 anyio 任务组取消
        的层级触发式重复投递会恰好打在第一个 shield 的**外层 await** 上，函数
        中断、`release` 分支不可达（HTTP 实测：租约悬置至 900s；引擎直连
        aclose 路径无重复投递所以从未暴露）。合入同一单元后：外层 await 无论
        被打断几次，受保护任务都在后台按"快照先落、收敛其次、还租在后"跑完
        ——取消落点不再决定收敛/归还是否开始。

        顺序不可反：反向会让新认领者先开跑、迟到的旧快照覆盖它的更新状态；
        且 save 内部还会续租，与"刚释放"打架。
        """
        if not task_id:
            return

        async def _save_then_release() -> None:
            """受保护收尾单元：同一任务里按序执行 save → converge → release
            （不可拆——拆分会让"取消落点"决定收敛/归还是否开始，见方法注释）。"""
            try:
                await checkpoint.save_from_live(
                    task_id=task_id,
                    live=agent.checkpoint_live if agent is not None else None,
                    trigger="on_disconnect",
                )
            except Exception as e:
                logger.warning(
                    "checkpoint on_disconnect failed (task=%s): %s",
                    log_safe(task_id), log_safe(e),
                )
            # 状态收敛兜底（第四波实机缺陷修复）：流已死 = 运行已死——不收理会
            # 永停"进行中"（见 converge_disconnected 长注释）。放进同一受保护
            # 单元：取消落点不得决定收敛是否发生（#300/F1 纪律同源）。
            try:
                await checkpoint.converge_disconnected(task_id)
            except Exception as e:
                logger.warning(
                    "断流收敛兜底失败 (task=%s): %s",
                    log_safe(task_id), log_safe(e),
                )
            if release_lease:
                try:
                    await checkpoint.release_task(task_id)
                except Exception as e:
                    logger.debug(
                        "checkpoint lease release on disconnect failed (task=%s): %s",
                        log_safe(task_id),
                        log_safe(e),
                    )

        try:
            await asyncio.shield(_save_then_release())
        except Exception as e:
            # CancelledError（BaseException）照常向外传播：调用方正处在取消
            # 路径上；受保护单元已在后台完成（shield 语义），本 except 只兜
            # 非取消类的意外故障。
            logger.warning(
                "checkpoint on_disconnect interrupted (task=%s): %s",
                log_safe(task_id), log_safe(e),
            )

    async def _checkpoint_error(
        self, task_id: str | None, agent: PERAgentLoop | None, exc: BaseException,
    ) -> None:
        """run 内异常：快照 + 标记 FAILED（写 last_error）；从不吞掉原异常。

        agent 为 None（T7：异常发生在 loop 构造之前的 pre-run 段）：没有活
        状态可落快照，仍按可恢复失败态收尾（同断流语义）。

        终态落库成功才发通知（best-effort，旁路绝不影响异常传播与任务状态）。
        """
        if not task_id:
            return
        try:
            await asyncio.shield(checkpoint.save_from_live(
                task_id=task_id,
                live=agent.checkpoint_live if agent is not None else None,
                trigger="on_error",
            ))
        except Exception as e:
            logger.warning("checkpoint on_error save failed (task=%s): %s", log_safe(task_id), log_safe(e))
        failure_text = f"{type(exc).__name__}: {exc}"
        ok = await checkpoint.mark_terminal(
            task_id,
            TaskStatus.FAILED,
            error=failure_text,
            error_type=type(exc).__name__,
        )
        if ok:
            await notify_task_terminal(task_id, TaskStatus.FAILED, error=failure_text)

    async def _finalize_task_terminal(
        self,
        task_id: str,
        *,
        failed_run: bool,
        failure_text: str | None = None,
    ) -> AgentEvent | None:
        """run 正常结束后置任务终态，并**消费** mark_terminal 的返回值
        （issue #301 批 A #10/#11；此前返回值被丢弃——DB 写失败时任务永远
        RUNNING 且无人知晓）。

        - failed_run（#11：无工具模式 LLM 全败，零产出）→ 如实置 FAILED 并写
          last_error（FAILED→RUNNING 是既有重试边：任务可被 resume 收敛）；
        - 否则置 COMPLETED。
        - 写失败（mark_terminal 已重试一次并留 ERROR 日志）→ 这里再响亮记一笔，
          并返回一条如实告知客户端的事件（调用方盖章 task_id 后补发）；成功时
          返回 None（零扰动，成功路径旧行为不变）。
        """
        if failed_run:
            ok = await checkpoint.mark_terminal(
                task_id,
                TaskStatus.FAILED,
                error=failure_text,
                error_type="llm_failure",
            )
        else:
            ok = await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)
        if ok:
            # 终态落库成功 → 站内通知（best-effort；通知失败绝不回滚任务状态）
            await notify_task_terminal(
                task_id,
                TaskStatus.FAILED if failed_run else TaskStatus.COMPLETED,
                error=failure_text if failed_run else None,
            )
            return None
        logger.error(
            "任务 %s 终态（%s）未能落库：任务行将停留原状态"
            "（任务列表可能仍显示进行中；可经 resume 收敛）",
            log_safe(task_id),
            "failed" if failed_run else "completed",
        )
        if failed_run:
            content = (
                "任务状态收尾失败：本次运行未产出有效回答，且任务记录未能"
                "标记失败（可稍后恢复重试）。"
            )
        else:
            content = (
                "任务状态收尾失败：运行结果已产出，但任务记录未能更新"
                "（任务列表可能仍显示进行中；稍后可恢复收敛）。"
            )
        return AgentEvent(type="error", content=content)

    async def _finalize_task_cancelled(self, task_id: str) -> AgentEvent | None:
        """取消收敛：置 CANCELLED 终态（**诚实终态**：不伪装 completed）；
        写失败同样消费返回值并响亮留痕（#301 批 A 同纪律）。
        终态落库成功 → 站内通知（best-effort 旁路）。"""
        ok = await checkpoint.mark_terminal(
            task_id,
            TaskStatus.CANCELLED,
            error="用户取消（协作式取消在检查点收敛）",
            error_type="user_cancelled",
        )
        if ok:
            await notify_task_terminal(task_id, TaskStatus.CANCELLED)
            return None
        logger.error(
            "任务 %s 取消终态未能落库：任务行将停留原状态（可经 resume 收敛）",
            log_safe(task_id),
        )
        return AgentEvent(
            type="error",
            content="任务取消状态收尾失败：任务记录未能更新（稍后可经恢复收敛）。",
        )

    async def search_and_chat(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        top_k: int = 5,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Hybrid mode: retrieve context first, then run PER agent.

        Combines traditional RAG retrieval with the full PER agent.
        """
        if not self.client:
            yield {"type": "error", "content": "LLM 未配置。"}
            return

        # Step 1: Retrieve context via RAG
        from app.dependencies import get_rag_pipeline
        from app.rag.errors import RetrievalUnavailableError

        pipeline = get_rag_pipeline()
        try:
            context_docs, _ = await pipeline.search_knowledge_base(
                query=query,
                organization_id=organization_id,
                top_k=top_k,
            )
        except RetrievalUnavailableError as e:
            # issue #292：检索故障不得静默当「没有上下文」——如实告知用户 +
            # 观测留痕（指标/日志），随后不带知识库上下文继续跑 PER 环。
            from app.core.prometheus import RAG_RETRIEVAL_UNAVAILABLE

            RAG_RETRIEVAL_UNAVAILABLE.labels(state=e.kind).inc()
            logger.warning("search_and_chat: knowledge retrieval unavailable (kind=%s): %s", e.kind, e)
            context_docs = []
            yield {
                "type": "warning",
                "content": "知识库检索服务暂不可用（故障/超时），本次回答未使用知识库上下文",
            }

        if context_docs:
            yield {
                "type": "context",
                "sources": [
                    {
                        "filename": d.get("filename", "Unknown"),
                        "score": d.get("score", 0),
                        "snippet": d.get("snippet", "")[:200],
                    }
                    for d in context_docs
                ],
            }

        # Step 2: Run PER agent with retrieved context
        config = AgentConfig(
            max_iterations=5,
            max_plan_steps=5,
        )

        agent = PERAgentLoop(
            openai_client=self.client,
            config=config,
            organization_id=organization_id,
            user_id=user_id,
        )

        async for event in agent.run(query, history=history, context_docs=context_docs):
            yield {
                "type": event.type,
                "content": event.content,
                "tool_name": event.tool_name,
                "plan_id": event.plan_id,
                "plan_step_id": event.plan_step_id,
                "plan_progress": event.plan_progress,
                "thinking_type": event.thinking_type,
                "reflection_result": event.reflection_result,
            }

    # ── Session management ──

    async def save_session_state(
        self,
        session_id: str,
        organization_id: int,
        user_id: int,
    ) -> None:
        """Save the full agent session state to Redis."""
        bridge = AgentMemoryBridge(
            agent_id=f"session:{session_id}",
            organization_id=organization_id,
            user_id=user_id,
        )
        state = bridge.export_state()
        try:
            from app.core.redis import redis_client
            if redis_client:
                key = f"agent:session_state:{session_id}"
                await redis_client.setex(
                    key, 86400 * 7,
                    json.dumps(state, ensure_ascii=False, default=str),
                )
                logger.info("Agent session state saved: %s", log_safe(session_id))
        except Exception as e:
            logger.warning("Failed to save session state: %s", log_safe(e))

    async def load_session_state(
        self,
        session_id: str,
        organization_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        """Load agent session state from Redis."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return None
            key = f"agent:session_state:{session_id}"
            raw = await redis_client.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("Failed to load session state: %s", log_safe(e))
        return None


# Singleton
agent_service = AgentService()
