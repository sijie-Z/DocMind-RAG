"""LLM 用量归一化（issue #202；指标拆分 issue #239 minor-9）——一处提取 usage → 指标 + ExecutionContext。

背景：PER 环的 LLM 调用点（planner 流式 / executor 主调 / executor 合成 /
reflector / loop 无工具分支 / reviewer / 工具内直连调用）此前要么丢弃
`response.usage`，要么复用 RAG 侧指标 —— 前者让 `ExecutionContext.total_tokens`
全仓零赋值，后者让指标无法区分 agent 与 RAG 两条链路。

**统一走这一个入口**，而不是"每个调用点各加一行"：点状方案在新增调用点
（subagent / retry / 流式）时必漏，本模块的存在就是让"漏"在评审时一眼可见。

指标口径（issue #239 minor-9）：
- agent 侧独立指标 `agent_tokens_total{model,direction}`（此前与 RAG 侧共用
  `rag_llm_tokens_total`，观测上无法拆分两条链路）；
- `rag_llm_tokens_total` 从此只由 RAG 链路写（`rag/pipeline.py` 的 report_tokens），
  其 "rag_" 前缀本就是 RAG 语义。
- `model` 标签取 `response.model`（真实 OpenAI 兼容响应均携带）；流式包的
  `SimpleNamespace(usage=...)`、测试替身等取不到时回退为 `"unknown"`。
"""

from app.core.prometheus import AGENT_TOKENS


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


def record_llm_usage(response, ctx=None) -> int:
    """提取 response.usage → Prometheus 指标 + ctx.total_tokens 累计。

    参数:
        response: 任何带 `.usage` 的对象（OpenAI 响应，或包了流式 usage 的
                  `SimpleNamespace(usage=...)`）；无 usage 时计 0、不抛错。
        ctx:      ExecutionContext（可选；鸭子类型，只要求 `add_tokens(int)`）。

    返回本次计入的 token 总数（prompt + completion；无 usage 时 0）——
    供 step 级记账（`complete_step(tokens=...)`）使用。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    try:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        # 防御：非数值字段（如 int() 转换不了的字符串）→ 计 0 不抛错。
        # 注意（评审 minor-5 更正）：MagicMock 不在此列——int(MagicMock()) == 1，
        # 它会记入极小值（1+1=2）而不是归零；此前的注释声称"MagicMock 计 0"不实。
        return 0
    model = _response_model(response)
    if prompt:
        AGENT_TOKENS.labels(model=model, direction="input").inc(prompt)
    if completion:
        AGENT_TOKENS.labels(model=model, direction="output").inc(completion)
    total = prompt + completion
    if total and ctx is not None:
        ctx.add_tokens(total)
    return total
