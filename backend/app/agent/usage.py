"""LLM 用量归一化（issue #202；指标拆分 issue #239 minor-9）——一处提取 usage → 指标 + ExecutionContext。

背景：PER 环的 LLM 调用点（planner 流式 / executor 主调 / executor 合成 /
reflector / loop 无工具分支 / reviewer / 工具内直连调用）此前要么丢弃
`response.usage`，要么复用 RAG 侧指标 —— 前者让 `ExecutionContext.total_tokens`
全仓零赋值，后者让指标无法区分 agent 与 RAG 两条链路。

**统一走这一个入口**，而不是"每个调用点各加一行"：点状方案在新增调用点
（subagent / retry / 流式）时必漏，本模块的存在就是让"漏"在评审时一眼可见。

issue #510 第一步：同一入口同时写 LLM 调用级 JSONL（`ExecutionContext.
record_llm_call` → `app.agent.llm_call_log`）。调用点可选传入
`latency_ms` / `messages`（用于 prompt_hash）/ `tool_calls` / `error`；
不传时字段如实留空/0，绝不臆造。

指标口径（issue #239 minor-9）：
- agent 侧独立指标 `agent_tokens_total{model,direction}`（此前与 RAG 侧共用
  `rag_llm_tokens_total`，观测上无法拆分两条链路）；
- `rag_llm_tokens_total` 从此只由 RAG 链路写（`rag/pipeline.py` 的 report_tokens），
  其 "rag_" 前缀本就是 RAG 语义。
- `model` 标签取 `response.model`（真实 OpenAI 兼容响应均携带）；流式包的
  `SimpleNamespace(usage=...)`、测试替身等取不到时回退为 `"unknown"`。
"""

import logging
from typing import Any

from app.core.prometheus import AGENT_TOKENS

logger = logging.getLogger(__name__)


def ctx_from_run_context(run_context):
    """从 RunContext 取挂载的 ExecutionContext（工具内调用记账，issue #239 minor-2）。

    执行器构造 RunContext 时把本轮 ExecutionContext 挂在 `extra["exec_ctx"]`
    （见 executor.py 工具调用点）；工具 handler 只需声明 `run_context` 形参，
    注册表按签名注入后即可透传给 `record_llm_usage`。取不到（工具被独立调用、
    无 run_context，或未挂 ctx）时返回 None —— 用量只进指标、不进任务级累计。
    """
    if run_context is None:
        return None
    extra = getattr(run_context, "extra", None)
    if isinstance(extra, dict):
        return extra.get("exec_ctx")
    return None


def _response_model(response) -> str:
    """提取响应的模型名（缺失/非字符串时回退 "unknown"，保证标签基数可控）。"""
    model = getattr(response, "model", None)
    return model if isinstance(model, str) and model else "unknown"


def _tool_call_count(response) -> int:
    """本次响应里模型请求的工具调用数（结构不符/缺失 → 0，绝不抛错）。"""
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return 0
        message = getattr(choices[0], "message", None)
        calls = getattr(message, "tool_calls", None)
        return len(calls) if calls else 0
    except Exception:  # noqa: BLE001 — 元数据提取失败按 0 处理
        return 0


def _record_jsonl(
    ctx,
    *,
    response,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float | None,
    messages: Any,
    prompt_hash: str | None,
    tool_calls: int | None,
    error: str | None,
) -> None:
    """写一行调用级 JSONL（ctx 缺失/鸭子类型不匹配时静默跳过）。

    `ExecutionContext.record_llm_call` 自身吞异常；这里再兜一层，保证任何
    ctx 替身（测试 stub / 未来形态）都不会让用量记账炸掉。
    """
    if ctx is None:
        return
    try:
        # getattr 也放进 try：未来若出现属性访问会抛错的鸭子类型 ctx，
        # 观测层异常不得穿进调用方的业务 except、改变返回语义（NIT-3）。
        record = getattr(ctx, "record_llm_call", None)
        if not callable(record):
            return
        record(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            prompt=messages,
            prompt_hash=prompt_hash,
            tool_calls=_tool_call_count(response) if tool_calls is None else tool_calls,
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 — 观测绝不阻断主流程
        logger.debug("LLM call JSONL record skipped: %s", exc)


def record_llm_usage(
    response,
    ctx=None,
    *,
    latency_ms: float | None = None,
    messages: Any = None,
    prompt_hash: str | None = None,
    tool_calls: int | None = None,
    error: str | None = None,
) -> int:
    """提取 response.usage → Prometheus 指标 + ctx.total_tokens + 调用级 JSONL。

    参数:
        response: 任何带 `.usage` 的对象（OpenAI 响应，或包了流式 usage 的
                  `SimpleNamespace(usage=...)`）；无 usage 时计 0、不抛错。
        ctx:      ExecutionContext（可选；鸭子类型，只要求 `add_tokens(int)`，
                  有 `record_llm_call(**kwargs)` 时同时写 JSONL）。
        latency_ms: 本次调用墙钟耗时（毫秒）；调用点不测时留 None。
        messages: 本次调用的提示词（list/dict/str）——仅用于计算 prompt_hash，
                  **正文不会落盘**。
        prompt_hash: 已算好的摘要（给定则优先于 messages）。
        tool_calls: 本次响应请求的工具调用数（None → 从 response 提取）。
        error: 失败调用的错误文本（成功调用为 None）。

    返回本次计入的 token 总数（prompt + completion；无 usage 时 0）——
    供 step 级记账（`complete_step(tokens=...)`）使用。
    """
    prompt = 0
    completion = 0
    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
        except (TypeError, ValueError):
            # 防御：非数值字段（如 int() 转换不了的字符串）→ 计 0 不抛错。
            # 注意（评审 minor-5 更正）：MagicMock 不在此列——int(MagicMock()) == 1，
            # 它会记入极小值（1+1=2）而不是归零；此前的注释声称"MagicMock 计 0"不实。
            prompt = 0
            completion = 0
    model = _response_model(response)
    if prompt:
        AGENT_TOKENS.labels(model=model, direction="input").inc(prompt)
    if completion:
        AGENT_TOKENS.labels(model=model, direction="output").inc(completion)
    total = prompt + completion
    if total and ctx is not None:
        ctx.add_tokens(total)
    _record_jsonl(
        ctx,
        response=response,
        model=model,
        tokens_in=prompt,
        tokens_out=completion,
        latency_ms=latency_ms,
        messages=messages,
        prompt_hash=prompt_hash,
        tool_calls=tool_calls,
        error=error,
    )
    return total


def record_llm_call_error(
    ctx,
    *,
    model: str | None = None,
    error: Any,
    latency_ms: float | None = None,
    messages: Any = None,
) -> None:
    """记录一次**失败**的 LLM 调用（无 response / 无 usage）。

    与 :func:`record_llm_usage` 共用同一 JSONL 形状：tokens_in/out=0、
    error 非空。ctx 缺失或形状不符时静默跳过——失败路径本就在降级处理中，
    观测不得再引入二次故障。
    """
    # 异常对象的 __str__ 自身可能抛错（自定义异常/半构造对象）：在调用
    # _record_jsonl 之前先安全求值，绝不把观测层的异常抛回调用方的 except。
    try:
        error_text = None if error is None else str(error)
    except Exception:  # noqa: BLE001 — 退化为类型名，保持可诊断
        error_text = type(error).__name__
    _record_jsonl(
        ctx,
        response=None,
        model=model or "unknown",
        tokens_in=0,
        tokens_out=0,
        latency_ms=latency_ms,
        messages=messages,
        prompt_hash=None,
        tool_calls=0,
        error=error_text,
    )
