"""体验第四波 @引用 —— 后端最小接线的判别测试。

被测契约：
- build_referenced_document_context：可用引用 → 关键摘要（query 相关分块）
  进上下文段；query 检索零命中 → 按 document_id 直读文档头部 chunk 兜底
  （第四波实机缺陷修复："总结这份文档"类元问题与正文零词汇重叠，此前
  零命中后只注入标题）；不存在 / 跨组织 / 检索失败 / 服务不可用 / 超上限
  → 逐条诚实警告（绝不静默）；全部失败 → document_context 为 None
  （不外发空段）。
- loop.run(document_context=...)：并入预规划上下文——planner 的 memory_context
  可见（与记忆同轨），**且 executor 步骤提示词同样可见**（默认工具模式下
  唯一写答案的模型；实机"只找到文档列表信息，没有拿到可总结的正文"的
  直接成因之一）；无引用时不注入（负例）。
- AgentService.chat：把引用解析成上下文段传给 run，警告以 warning 事件先于
  任务事件外发（含 task_id）。

变异点：
- builder 把「跨组织」分支删掉 → 跨组织用例红（文档泄漏进上下文）；
- builder 不做直读兜底 → 直读用例红（零命中后只剩标题 + 降级警告）；
- loop 不拼 document_context → planner 捕获用例红；
- loop 不把 document_context 传给 executor.execute → executor 捕获用例红；
- chat 不传 referenced_document_ids → service 传参用例红。
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent import service as service_mod
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.agent.service import AgentService, build_referenced_document_context
from app.core.database import Base
from app.core.elasticsearch import ElasticsearchTools
from app.models.document import Document, DocumentType
from app.rag.retriever import HybridRetriever

DOC_CTX = "## 用户显式引用的知识库文档（回答时优先依据）\n### [1] 预算报告（document_id: d1）\nQ3 预算要点……"


# ── 假件 ─────────────────────────────────────────────────────────────────────


class _FakePipeline:
    def __init__(self, texts: dict[str, list[str]] | None = None, fail: bool = False):
        self.texts = texts or {}
        self.fail = fail
        self.calls: list[dict] = []

    async def search_knowledge_base(
        self,
        query,
        organization_id,
        top_k=5,
        document_ids=None,
        debug=False,
        search_mode=None,
    ):
        self.calls.append({"query": query, "document_ids": document_ids, "org": organization_id})
        if self.fail:
            raise RuntimeError("ES 不可用（替身）")
        results = [
            {"document_id": did, "text": t, "snippet": t[:50], "score": 0.9}
            for did in (document_ids or [])
            for t in self.texts.get(did, [])
        ]
        return results, None


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}

    def reset_working(self):
        self.results = {}

    def get_all_results(self):
        return dict(self.results)

    def store_step_result(self, step_id, result):
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def get_context_for_query(self, query):
        return ""

    async def record_experience(self, **kwargs):
        pass

    async def record_interaction(self, user_input, assistant_response):
        pass


class _CapturingPlanner:
    """记录每次 plan() 收到的 memory_context（@引用注入的观测点）。"""

    def __init__(self):
        self.memory_contexts: list[str] = []

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.memory_contexts.append(memory_context)
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(
            type="plan_step",
            plan_id="plan-1",
            plan_step_id="s1",
            content="第1步",
            dependencies=[],
        )
        yield AgentEvent(
            type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成"
        )


def _capturing_client():
    async def _create(*, messages, tools=None, **kwargs):
        msg = SimpleNamespace(content="结论", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


def _loop_cfg() -> AgentConfig:
    return AgentConfig(
        enable_memory=False,
        enable_reflection=False,
        enable_skills=False,
        enable_experience=False,
        enable_thinking=False,
        enable_planning=True,
    )


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))
    # 直读兜底（第四波修复）走 ElasticsearchTools：默认桩成"索引无命中"——
    # 既有用例保持环境封闭（不发起真实 ES 调用）；需要命中/故障形态的用例
    # 在各自用例体内覆写。
    monkeypatch.setattr(
        ElasticsearchTools,
        "search_documents",
        AsyncMock(return_value={"hits": {"hits": []}}),
    )


@pytest.fixture
async def doc_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'refdocs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import app.core.database as core_db

    monkeypatch.setattr(core_db, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def _add_doc(
    factory, doc_id: str, *, org: int = 1, title: str = "预算报告", description=None
):
    async with factory() as db:
        db.add(
            Document(
                id=doc_id,
                filename=f"{doc_id}.pdf",
                file_path=f"/tmp/{doc_id}.pdf",
                file_size=10,
                file_type=DocumentType.PDF,
                title=title,
                description=description,
                organization_id=org,
                uploaded_by=1,
            )
        )
        await db.commit()


# ── builder 单元 ─────────────────────────────────────────────────────────────


class TestBuildReferencedDocumentContext:
    async def test_available_doc_injects_excerpt_without_warnings(self, doc_factory):
        await _add_doc(doc_factory, "d1")
        pipe = _FakePipeline(texts={"d1": ["Q3 预算要点：营收增长 12%", "成本下降 3%"]})

        ctx, warnings = await build_referenced_document_context(["d1"], "预算", 1, pipeline=pipe)

        assert ctx is not None
        assert "预算报告" in ctx and "d1" in ctx
        assert "营收增长 12%" in ctx
        assert warnings == []
        # 检索被限定到引用文档（document_ids 过滤）
        assert pipe.calls[0]["document_ids"] == ["d1"]

    async def test_missing_doc_warns_and_no_empty_section(self, doc_factory):
        ctx, warnings = await build_referenced_document_context(
            ["ghost"], "q", 1, pipeline=_FakePipeline()
        )
        assert ctx is None
        assert len(warnings) == 1 and "不存在" in warnings[0]

    async def test_cross_org_doc_warns_not_leaked(self, doc_factory):
        await _add_doc(doc_factory, "d2", org=2)
        ctx, warnings = await build_referenced_document_context(
            ["d2"], "q", 1, pipeline=_FakePipeline()
        )
        assert ctx is None
        assert len(warnings) == 1 and "无权访问" in warnings[0]

    async def test_retrieval_failure_falls_back_to_description_with_warning(self, doc_factory):
        await _add_doc(doc_factory, "d3", description="涵盖 Q3 预算编制口径")
        ctx, warnings = await build_referenced_document_context(
            ["d3"],
            "q",
            1,
            pipeline=_FakePipeline(fail=True),
        )
        assert ctx is not None and "涵盖 Q3 预算编制口径" in ctx
        assert any("检索失败" in w for w in warnings)

    async def test_pipeline_unavailable_warns(self, doc_factory):
        await _add_doc(doc_factory, "d4")
        ctx, warnings = await build_referenced_document_context(["d4"], "q", 1, pipeline=None)
        assert ctx is not None and "预算报告" in ctx
        assert any("摘要服务不可用" in w for w in warnings)

    async def test_over_cap_ids_warned_not_silent(self, doc_factory):
        ids = [f"missing-{i}" for i in range(12)]
        ctx, warnings = await build_referenced_document_context(
            ids, "q", 1, pipeline=_FakePipeline()
        )
        assert ctx is None
        assert sum("超出单次上限" in w for w in warnings) == 2
        assert len(warnings) == 12  # 10 条（不存在）+ 2 条（超出）——一条不吞

    async def test_query_empty_falls_back_to_direct_head_chunk_read(self, doc_factory, monkeypatch):
        """第四波实机缺陷回归（红基线）：“总结这份文档”类元问题与正文零词汇
        重叠——query 检索（含质量门）零命中后，正文必须按 document_id 直读
        头部 chunk 兜底，而不是只注入标题（实机："我目前只找到文档列表信息，
        没有拿到可总结的正文内容"）。

        变异点：删掉直读兜底 → 本用例红（excerpt 空、仅标题 + 降级警告）。
        """
        await _add_doc(doc_factory, "d5")
        monkeypatch.setattr(
            ElasticsearchTools,
            "search_documents",
            AsyncMock(
                return_value={
                    "hits": {
                        "hits": [
                            {"_source": {"content": "# Contributing to DocMind\n欢迎贡献……"}},
                            {"_source": {"content": "行为准则：Contributor Covenant……"}},
                        ]
                    }
                }
            ),
        )
        pipe = _FakePipeline()  # 检索臂正常执行但零命中（无 texts）

        ctx, warnings = await build_referenced_document_context(
            ["d5"],
            "总结这份文档",
            1,
            pipeline=pipe,
        )

        assert ctx is not None and "欢迎贡献" in ctx, "正文必须经直读进入上下文"
        assert warnings == [], "直读成功 = 无降级，不得外发误导性警告"
        assert pipe.calls, "query 检索仍应先执行（相关分块优先，直读是兜底）"

    async def test_direct_read_also_empty_keeps_description_fallback_with_warning(
        self,
        doc_factory,
        monkeypatch,
    ):
        """直读兜底同样拿不到正文（ES 故障/未索引）→ 退回既有降级语义：
        描述兜底 + 诚实警告（绝不静默）。"""
        await _add_doc(doc_factory, "d6", description="只有描述，没有可读正文")
        monkeypatch.setattr(
            ElasticsearchTools,
            "search_documents",
            AsyncMock(side_effect=RuntimeError("ES 不可用（替身）")),
        )

        ctx, warnings = await build_referenced_document_context(
            ["d6"],
            "q",
            1,
            pipeline=_FakePipeline(),
        )

        assert ctx is not None and "只有描述" in ctx
        assert any("未检索到相关正文" in w for w in warnings)


# ── loop 接线 ────────────────────────────────────────────────────────────────


class TestLoopDocumentContextWiring:
    async def test_document_context_reaches_planner(self):
        loop = PERAgentLoop(_capturing_client(), _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        planner = _CapturingPlanner()
        loop._planner = planner

        events = [ev async for ev in loop.run("查预算", document_context=DOC_CTX)]

        assert any(ev.type == "done" for ev in events)
        assert len(planner.memory_contexts) >= 1
        assert "用户显式引用的知识库文档" in planner.memory_contexts[0]

    async def test_without_document_context_planner_unaffected(self):
        loop = PERAgentLoop(_capturing_client(), _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        planner = _CapturingPlanner()
        loop._planner = planner

        _ = [ev async for ev in loop.run("查预算")]

        assert "用户显式引用的知识库文档" not in planner.memory_contexts[0]

    async def test_document_context_reaches_executor_step_prompt(self):
        """工具模式（默认）下唯一写答案的是 executor 的步骤 LLM——@引用摘要
        此前只到 planner/toolless，答案模型全程看不到正文（第四波实机
        “只找到文档列表信息”的直接成因之一）。钉住：步骤提示词必须包含
        引用段（捕获真实发给 LLM 的 messages）。

        变异点：loop 不把 document_context 传给 executor.execute → 捕获红。
        """
        captured: list[list] = []

        async def _create(*, messages=None, tools=None, **kwargs):
            captured.append(messages)
            msg = SimpleNamespace(content="结论", tool_calls=None, role="assistant")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        client = MagicMock()
        client.chat.completions.create = _create
        loop = PERAgentLoop(client, _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        loop._planner = _CapturingPlanner()

        _ = [ev async for ev in loop.run("查预算", document_context=DOC_CTX)]

        assert captured, "executor 步骤必须发起过 LLM 调用（前置条件）"
        system_prompts = [
            m["content"] for msgs in captured if msgs for m in msgs if m.get("role") == "system"
        ]
        assert any("用户显式引用的知识库文档" in p for p in system_prompts), (
            "引用段必须进入 executor 步骤提示词（planner 之外的第二注入点）"
        )

    async def test_without_document_context_executor_prompt_unaffected(self):
        captured: list[list] = []

        async def _create(*, messages=None, tools=None, **kwargs):
            captured.append(messages)
            msg = SimpleNamespace(content="结论", tool_calls=None, role="assistant")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        client = MagicMock()
        client.chat.completions.create = _create
        loop = PERAgentLoop(client, _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        loop._planner = _CapturingPlanner()

        _ = [ev async for ev in loop.run("查预算")]

        system_prompts = [
            m["content"] for msgs in captured if msgs for m in msgs if m.get("role") == "system"
        ]
        assert system_prompts, "executor 步骤必须发起过 LLM 调用（前置条件）"
        assert not any("用户显式引用的知识库文档" in p for p in system_prompts), (
            "无引用时不得注入引用段（负例）"
        )


# ── service.chat 接线 ────────────────────────────────────────────────────────


class _StubLoop:
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        self.cancel_check = None
        self.paused_for_approval = False
        self.paused_for_plan_approval = False
        self.run_cancelled = False
        self.run_failed = False
        self.checkpoint_live = None

    def _get_tools(self):
        return []

    async def run(
        self,
        query,
        history=None,
        task_id=None,
        session_id=None,
        document_context=None,
        request_id=None,
    ):
        _StubLoop.last_kwargs = {
            "document_context": document_context,
            "task_id": task_id,
            "request_id": request_id,
        }
        yield AgentEvent(type="done", content="")


class TestServiceChatWiring:
    async def test_warns_missing_refs_and_passes_context_to_run(self, doc_factory, monkeypatch):
        await _add_doc(doc_factory, "d9")
        pipe = _FakePipeline(texts={"d9": ["Q3 要点"]})
        monkeypatch.setattr(service_mod, "PERAgentLoop", _StubLoop)
        # get_rag_pipeline 在 chat 函数体内 import：打桩来源模块属性
        import app.dependencies as deps

        monkeypatch.setattr(deps, "get_rag_pipeline", lambda: pipe)

        svc = AgentService(openai_client=MagicMock())
        cfg = _loop_cfg()
        cfg.enable_checkpoint = True

        events = [
            ev
            async for ev in svc.chat(
                "查预算",
                organization_id=1,
                user_id=1,
                config=cfg,
                referenced_document_ids=["d9", "ghost"],
                request_id="req-chat-510",
            )
        ]

        warn_events = [ev for ev in events if ev.type == "warning"]
        assert any("ghost" in ev.content and "不存在" in ev.content for ev in warn_events)
        # 警告携带 task_id（checkpoint 开着时建了任务行）
        assert all(ev.task_id for ev in warn_events)
        assert "用户显式引用的知识库文档" in _StubLoop.last_kwargs["document_context"]
        # issue #510 第二步：service 必须把本次请求编号透传给 agent.run
        assert _StubLoop.last_kwargs["request_id"] == "req-chat-510"
        assert any(ev.type == "done" for ev in events)

    async def test_no_refs_no_document_context(self, doc_factory, monkeypatch):
        monkeypatch.setattr(service_mod, "PERAgentLoop", _StubLoop)
        import app.dependencies as deps

        monkeypatch.setattr(deps, "get_rag_pipeline", lambda: _FakePipeline())

        svc = AgentService(openai_client=MagicMock())
        cfg = _loop_cfg()
        cfg.enable_checkpoint = True

        _ = [ev async for ev in svc.chat("q", organization_id=1, user_id=1, config=cfg)]
        assert _StubLoop.last_kwargs["document_context"] is None


# ── resume 链路：断流恢复不丢 @引用上下文（T14）─────────────────────────────


def _resume_payload(*, steps, include_plan=True, document_context=None):
    """模拟断流时落下的快照信封（与 checkpoint.build_envelope 同形）。"""
    ctx = ExecutionContext(query="原始问题", task_id="t-1")
    if document_context is not None:
        ctx.document_context = document_context
    plan = Plan(
        id="p1",
        goal="目标",
        steps=steps,
        completed_steps=sum(1 for s in steps if s.status == "completed"),
    )
    return {
        "v": 1,
        "session_id": "sess-1",
        "ctx": ctx.to_checkpoint_dict(),
        "plan": plan.to_checkpoint_dict() if include_plan else None,
        "history": [{"role": "user", "content": "原始问题"}],
        "config": _loop_cfg().to_dict(),
        "run": {"execution_mode": "dag", "replan_attempts": 0, "replan_feedback": ""},
        "partial_output": "",
    }


class _StubResumeLoop:
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        self.cancel_check = None
        self.paused_for_approval = False
        self.paused_for_plan_approval = False
        self.run_cancelled = False
        self.run_failed = False
        self.checkpoint_live = None

    def _get_tools(self):
        return []

    async def run(
        self,
        query,
        *,
        task_id=None,
        resume=None,
        session_id=None,
        document_context=None,
        history=None,
        request_id=None,
    ):
        _StubResumeLoop.last_kwargs = {
            "document_context": document_context,
            "resume": resume,
            "task_id": task_id,
            "request_id": request_id,
        }
        yield AgentEvent(type="done", content="")


class TestResumeDocumentContext:
    def test_snapshot_roundtrip_carries_document_context(self, caplog):
        """快照字段：document_context 进 to_checkpoint_dict、from_checkpoint_dict
        还原；值级漂移（非字符串）降级为空串并留 warning（与既有值级闸门同口径）。

        变异点：快照不序列化该字段 → KeyError / 重建丢失 → 前两条断言红；
        读侧不做类型闸门 → 坏值进运行态 → 降级断言红。
        """
        ctx = ExecutionContext(query="q")
        ctx.document_context = DOC_CTX

        data = ctx.to_checkpoint_dict()
        assert data["document_context"] == DOC_CTX
        assert ExecutionContext.from_checkpoint_dict(data).document_context == DOC_CTX

        data["document_context"] = 123
        with caplog.at_level(logging.WARNING):
            degraded = ExecutionContext.from_checkpoint_dict(data)
        assert degraded.document_context == "", "坏值绝不进入运行态（降级为无引用段）"
        assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕"

    async def test_resume_with_plan_delivers_document_context_to_executor(self):
        """断流（有 plan）后 resume：executor 步骤提示词仍带引用段。

        变异点：resume 链路不恢复/不透传 document_context → executor 系统提示词
        无引用段 → 红；恢复后的 ctx 不回填 → 下一份快照丢引用段 → 尾断言红。
        """
        captured: list[list] = []

        async def _create(*, messages=None, tools=None, **kwargs):
            captured.append(messages)
            msg = SimpleNamespace(content="结论", tool_calls=None, role="assistant")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        client = MagicMock()
        client.chat.completions.create = _create
        loop = PERAgentLoop(client, _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        steps = [
            PlanStep(id="s1", description="一", status="completed", result="R1"),
            PlanStep(id="s2", description="二", dependencies=["s1"]),
        ]
        payload = _resume_payload(steps=steps, document_context=DOC_CTX)

        events = [ev async for ev in loop.run("原始问题", task_id="t-1", resume=payload)]

        assert any(ev.type == "done" for ev in events)
        assert captured, "executor 步骤必须发起过 LLM 调用（前置条件）"
        system_prompts = [
            m["content"] for msgs in captured if msgs for m in msgs if m.get("role") == "system"
        ]
        assert any("用户显式引用的知识库文档" in p for p in system_prompts), (
            "断流快照恢复后，executor 步骤提示词必须仍带引用段"
        )
        assert loop.checkpoint_live["ctx"].document_context == DOC_CTX, (
            "恢复后的运行态必须继续携带引用段（下一次快照不得丢失）"
        )

    async def test_resume_without_plan_delivers_document_context_to_planner(self):
        """崩溃于规划阶段（快照无 plan）的 resume：planner 仍须看到引用段。

        变异点：resume 的重规划路径不恢复 document_context → planner 的
        memory_context 无引用段 → 红。
        """
        loop = PERAgentLoop(_capturing_client(), _loop_cfg(), organization_id=1, user_id=0)
        loop.memory = _FakeMemory()
        planner = _CapturingPlanner()
        loop._planner = planner
        payload = _resume_payload(steps=[], include_plan=False, document_context=DOC_CTX)

        events = [ev async for ev in loop.run("原始问题", task_id="t-1", resume=payload)]

        assert any(ev.type == "done" for ev in events)
        assert planner.memory_contexts, "无 plan 的 resume 必须重跑规划阶段（前置条件）"
        assert "用户显式引用的知识库文档" in planner.memory_contexts[0], (
            "resume 重规划时 planner 必须看到引用段"
        )

    async def test_service_resume_chat_passes_snapshot_context_to_run(self, monkeypatch):
        """service.resume_chat：从快照 ctx 取回 document_context 并透传给
        agent.run（签名 + 调用点）。

        变异点：resume_chat 不向 run 传 document_context → 桩记录为 None → 红。
        """
        payload = _resume_payload(
            steps=[PlanStep(id="s1", description="一", status="completed", result="R1")],
            document_context=DOC_CTX,
        )
        task = SimpleNamespace(organization_id=1, user_id=1)
        monkeypatch.setattr(service_mod, "PERAgentLoop", _StubResumeLoop)
        monkeypatch.setattr(
            service_mod.checkpoint,
            "load_for_resume",
            AsyncMock(return_value=(task, payload)),
        )
        monkeypatch.setattr(
            service_mod.checkpoint,
            "mark_terminal",
            AsyncMock(return_value=True),
        )
        monkeypatch.setattr(service_mod, "notify_task_terminal", AsyncMock())

        svc = AgentService(openai_client=MagicMock())
        events = [
            ev
            async for ev in svc.resume_chat(
                "t-1", user_id=1, claim=False, request_id="req-resume-510"
            )
        ]

        assert any(ev.type == "done" for ev in events)
        assert _StubResumeLoop.last_kwargs["resume"] is payload
        assert _StubResumeLoop.last_kwargs["request_id"] == "req-resume-510"
        assert _StubResumeLoop.last_kwargs["document_context"] == DOC_CTX, (
            "resume 必须把快照里的引用上下文透传给 agent.run"
        )
