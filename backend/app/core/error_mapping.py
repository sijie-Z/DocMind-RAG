"""对外错误口径（W0-5）：SSE 错误帧 / AgentEvent 错误文本的映射与脱敏。

客户端可看到的错误只应有三类：

1. **可预期拒绝**（无组织等 403 语义）——沿用 domain 异常里既有的用户可读文案；
2. **配置诊断**（LLM 未配置）——既有诚实文案，明确指向配置项；
3. **未预期异常**——通用文案（可带请求编号），完整异常与堆栈只进服务端日志。

实现分两层：

- :func:`public_exception_message`：端点兜底 ``except`` 用，按异常类型映射；
  仅 403 ``AuthorizationError`` 的文案原样放行（domain 设计中面向用户），
  其余 4xx/5xx 与所有未预期异常一律通用文案（4xx 的 message 也可能被
  调用方拼进 ``str(e)``，不能盲信）。
- :func:`public_agent_event_content`：AgentEvent 转发边界用。``error`` 类型
  只放行完整匹配白名单的项目自撰文案（未知内容一律降级——白名单外无法逐条
  证明不含内部细节）；``tool_error`` 保留已确认安全的固定文案，带原始异常
  插值的已知构造统一改写为通用文案。
- :func:`public_diagnostic_content`：``run_report`` / ``execution_context``
  等诊断载荷里的失败/异常片段做文本脱敏（保留结构与可观测字段）。

维护约定：新增**可对外**错误文案 → 加入下方白名单并补测试；
**仅用于排障**的内部细节 → 只写日志，绝不进入事件内容。
"""

from __future__ import annotations

import re
from typing import Any

from app.exceptions import AuthorizationError

# ── 对外固定文案 ────────────────────────────────────────────────────────────
GENERIC_SERVER_ERROR = "服务器处理出错，请稍后重试"
GENERIC_MODEL_ERROR = "模型调用失败，请稍后重试"
GENERIC_TOOL_ERROR = "工具执行失败，请稍后重试"
GENERIC_RAG_ERROR = "知识库问答出错，请稍后重试"
ORG_REQUIRED_MESSAGE = "当前用户未加入任何组织，无权访问组织数据"

# 无组织 / 缺运行上下文的内部 ValueError 特征（agent/service.py、loop.py、
# subagent.py 等 fail-closed 入口）。这些异常文本含内部参数名，绝不能外发。
_ORG_ERROR_MARKERS = ("缺少 organization_id", "无组织上下文", "未加入任何组织")


def _is_missing_org_error(exc: BaseException) -> bool:
    return isinstance(exc, ValueError) and any(marker in str(exc) for marker in _ORG_ERROR_MARKERS)


# 请求编号会进客户端文案与日志——只接受安全字符集/长度（防回显放大与日志注入；
# W0-9 若未来透传入站 X-Request-ID，这里仍是一道闸）。
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _safe_request_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _REQUEST_ID_RE.fullmatch(text) else None


def resolve_request_id(request: Any) -> str | None:
    """读取请求编号：优先 W0-9 的 ``get_request_id()`` contextvar。

    W0-9 已提供 ``get_request_id``（普通 HTTP handler/依赖可见）；catch-all/500
    场景其返回 None，回退到既有 ``RequestIDMiddleware`` 写入的
    ``request.state.request_id``——不另造 contextvar/中间件。运行环境未包含
    W0-9 时（ImportError）同样走 state 回退。返回值经过字符集/长度校验；
    调用方仍需用 ``log_safe`` 记录该值（防日志注入）。
    """
    try:
        from app.core.request_id import get_request_id  # W0-9（contextvar 单源）
    except ImportError:  # pragma: no cover - 未包含 W0-9 的运行环境兼容回退
        get_request_id = None  # type: ignore[assignment]
    if get_request_id is not None:
        try:
            request_id = get_request_id()
        except Exception:  # contextvar 读取失败不应影响错误响应
            request_id = None
        safe_id = _safe_request_id(request_id)
        if safe_id:
            return safe_id
    return _safe_request_id(getattr(getattr(request, "state", None), "request_id", None))


def public_exception_message(
    exc: BaseException,
    request_id: str | None = None,
    generic: str = GENERIC_SERVER_ERROR,
) -> str:
    """把端点兜底捕获的异常映射为对外文案。

    - 无组织 ValueError → 403 语义的用户可读拒绝文案；
    - 403 ``AuthorizationError`` → 既有 domain 文案（跨组织会话拒绝等按设计
      面向用户）；
    - 其余（含其它 4xx / 5xx ``AppError`` 与所有未预期异常）→ ``generic``
      （默认通用文案）+ 请求编号。

    其它 4xx 也不直通：仓库里存在 ``ValidationError(f"...{str(e)}")`` 形态，
    4xx 的 message 并不能保证不含内部细节。
    """
    if _is_missing_org_error(exc):
        return ORG_REQUIRED_MESSAGE
    if isinstance(exc, AuthorizationError):
        return exc.message
    text = generic
    if request_id:
        text = f"{text}（请求编号：{request_id}）"
    return text


# ── AgentEvent error / tool_error 白名单 ────────────────────────────────────
# error 事件：完整匹配（锚定）才放行——固定文案不得被拼接异常尾部穿透。
_SAFE_ERROR_MESSAGES = re.compile(
    r"^(?:"
    r"LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。"
    r"|LLM 服务未配置（未设置 DEEPSEEK_API_KEY），无法生成回答，请联系管理员配置后重试"
    r"|当前用户未加入任何组织，无权访问组织数据"
    r"|当前用户未加入任何组织，无法进行知识库问答；请先加入组织后重试"
    r"|系统当前不存在任何组织，无法确定组织上下文；请先创建组织后重试"
    r"|无法为当前任务生成执行计划"
    r"|任务状态收尾失败：本次运行未产出有效回答，且任务记录未能标记失败（可稍后恢复重试）。"
    r"|任务状态收尾失败：运行结果已产出，但任务记录未能更新（任务列表可能仍显示进行中；"
    r"稍后可恢复收敛）。"
    r"|任务取消状态收尾失败：任务记录未能更新（稍后可经恢复收敛）。"
    r"|Agent 执行出错，请稍后重试(?:（请求编号：[0-9a-zA-Z-]{1,64}）)?"
    r"|Agent 计划审批续跑出错，请稍后重试(?:（请求编号：[0-9a-zA-Z-]{1,64}）)?"
    r"|Executor Agent: 等待执行消息超时"
    r"|Executor Agent: 未知消息类型 [A-Za-z_][\w]*"
    r")$"
)

# error 事件：已知携带原始异常插值的构造 → 通用文案。
_ERROR_REWRITES = (
    ("LLM Error", GENERIC_MODEL_ERROR),
    ("LLM 调用失败", GENERIC_MODEL_ERROR),
)

# tool_error 事件：完整匹配即放行的自撰文案（正则锚定，防止被拼接尾部细节）。
_SAFE_TOOL_ERROR_FULL = re.compile(
    r"^(?:"
    r"工具 \S+ 不在当前上下文允许的工具集内，已拒绝执行"
    r"|审批服务不可用（暂停状态无法持久化），工具 \S+ 未执行"
    r"|LLM 服务连续失败，熔断开启，快速失败"
    r")$"
)

# tool_error 事件：人工备注属于用户自己刚输入的内容（同请求可见），放行。
_SAFE_TOOL_ERROR_PREFIXES = ("人工审批拒绝执行工具",)

_RETRY_EXHAUSTED_RE = re.compile(r"^步骤失败（已重试 (\d+) 次）")

# RAG 通道（RAGEvent.content）的自撰错误文案白名单：当前生产点全部是固定文案，
# 但同一“error 帧”面未来不得因新增 ``str(e)`` 而绕开白名单。
_SAFE_RAG_ERROR_MESSAGES = re.compile(
    r"^(?:"
    r"当前用户未加入任何组织，无法进行知识库问答；请先加入组织后重试"
    r"|LLM 服务未配置（未设置 DEEPSEEK_API_KEY），无法生成回答，请联系管理员配置后重试"
    r"|知识库检索服务暂不可用（故障/超时），严格模式无法在没有文档依据的情况下作答；"
    r"请稍后重试"
    r"|知识库中未找到与问题足够相关的内容，严格模式无法在没有相关文档依据的情况下作答；"
    r"请尝试换个问法，或联系管理员补充相关文档"
    r"|无权访问该会话"
    r"|没有可重新生成的回答；请刷新会话后重试"
    r"|会话不存在或无权访问；请刷新后重试"
    r")$"
)


def _tool_generic(tool_name: str) -> str:
    name = (tool_name or "").strip()
    return f"工具 {name} 执行失败，请稍后重试" if name else GENERIC_TOOL_ERROR


def public_agent_event_content(event_type: str, content: Any, tool_name: str = "") -> Any:
    """AgentEvent → SSE 前的内容口径。

    - ``error``：白名单外一律降级为通用文案（可含原始异常/内部类名，无法逐条
      证明安全）。
    - ``tool_error``：保留已知安全的固定文案；``步骤失败（已重试 N 次）`` /
      ``工具参数不是合法 JSON`` 等带插值的构造保留自撰前缀、丢弃异常尾部；
      其余未知内容降级为通用工具失败文案（保留工具名这一可对外上下文）。
    """
    if not isinstance(content, str):
        # error / tool_error 的 content 契约是字符串；异常形态一律按失败降级，
        # 其余事件类型（如结构化诊断）原样返回，由调用方决定是否脱敏。
        if event_type == "error":
            return GENERIC_SERVER_ERROR
        if event_type == "tool_error":
            return _tool_generic(tool_name)
        return content
    text = content.strip()
    if not text:
        return GENERIC_SERVER_ERROR if event_type == "error" else _tool_generic(tool_name)

    if event_type == "error":
        if _SAFE_ERROR_MESSAGES.match(text):
            return content
        for prefix, replacement in _ERROR_REWRITES:
            if text.startswith(prefix):
                return replacement
        return GENERIC_SERVER_ERROR

    if event_type == "tool_error":
        if _SAFE_TOOL_ERROR_FULL.match(text):
            return content
        for prefix in _SAFE_TOOL_ERROR_PREFIXES:
            if text.startswith(prefix):
                return content
        retry_match = _RETRY_EXHAUSTED_RE.match(text)
        if retry_match:
            return f"步骤失败（已重试 {retry_match.group(1)} 次）"
        if text.startswith("工具参数不是合法 JSON，已拒绝执行"):
            return "工具参数不是合法 JSON，已拒绝执行"
        if text.startswith("budget_exceeded:"):
            return "本次运行已达 token 预算上限，剩余步骤已跳过"
        for prefix, replacement in _ERROR_REWRITES:
            if text.startswith(prefix):
                return replacement
        return _tool_generic(tool_name)

    return content


def public_rag_error_content(content: Any) -> str:
    """RAG 通道 error 帧的同一口径（白名单外一律通用文案）。"""
    if isinstance(content, str) and _SAFE_RAG_ERROR_MESSAGES.match(content.strip()):
        return content
    return GENERIC_RAG_ERROR


# ── 诊断载荷脱敏（run_report / execution_context / thinking）────────────────
# 真实 traceback 以 "Traceback (most recent call last):" 开头；按空行截断，
# 避免把后面的报告正文一起吞掉。
_TRACEBACK_BLOCK = re.compile(r"Traceback \(most recent call last\):?(?:\r?\n(?!\r?\n)[^\n]*)+")
# 供应商/驱动异常原文的常见形态（不带 "XxxError:" 前缀，前面几个正则接不住）。
_PROVIDER_ERROR_DETAIL = re.compile(
    r"(?:Error code:\s*\d+[^\n]*|\bRequest timed out\.|\bConnection error\.)"
)
_EXCEPTION_DETAIL = re.compile(r"\b[A-Za-z_][\w.]*(?:Error|Exception|Timeout)\b\s*:[^\n]*")
_CLASS_REPR = re.compile(r"<class '[^']+'>")
_FILE_PATH = re.compile(
    r"[A-Za-z]:\\[^\s\"'<>]+"
    r"|(?<![\w:/])/(?:[^\s\"'<>]+/)*[^\s\"'<>]+"
    # 相对源码路径：排除 URL（`://`）或已在前缀中的片段，避免过度脱敏。
    r"|(?<![\w/.:-])(?:app|backend|frontend|tests|deploy|skills)/[\w./-]+"
)
# SQL 只对常见的大写驱动回显做脱敏（避免把英文散文里的
# "select ... from ..." 误伤；错误帧路径本身由白名单兜底）。
_SQL = re.compile(
    r"\b(?:SELECT\b[^\n;]*\bFROM\b|INSERT\s+INTO\b"
    r"|UPDATE\s+[A-Za-z_][\w.]*\s+SET\b|DELETE\s+FROM\b"
    r"|DROP\s+TABLE\b|ALTER\s+TABLE\b)"
)
_INTERNAL_MODULE = re.compile(
    r"\b(?:sqlalchemy|aiomysql|asyncpg|pymysql|openai|httpx|redis"
    r"|elasticsearch|starlette|fastapi|pydantic|greenlet)\.[\w.]+"
    r"|site-packages|__main__"
)
_REPORT_ERROR_LINE = re.compile(r"(?m)^(.*?\bERROR=).*$")
_REPORT_FAILURE_LINE = re.compile(r"(?m)^(\s*FAILURE:\s).*$")

# 结构化诊断里按“失败细节”处理的键（值整体替换，避免未知异常文本原样外发）。
_FAILURE_KEYS = frozenset({"failures", "error", "error_context", "run_failure"})
# 反思/对抗审查失败态的 decision action：reasoning 里的异常原文整体替换。
_FAILURE_DECISION_ACTIONS = frozenset({"error", "unavailable", "failed", "failure"})
_FAILURE_PLACEHOLDER = "[内部失败细节已省略]"


def _redact_text(text: str) -> str:
    text = _TRACEBACK_BLOCK.sub("[内部堆栈已省略]", text)
    text = _REPORT_ERROR_LINE.sub(r"\1" + _FAILURE_PLACEHOLDER, text)
    text = _REPORT_FAILURE_LINE.sub(r"\1" + _FAILURE_PLACEHOLDER, text)
    text = _PROVIDER_ERROR_DETAIL.sub(_FAILURE_PLACEHOLDER, text)
    text = _EXCEPTION_DETAIL.sub(_FAILURE_PLACEHOLDER, text)
    text = _CLASS_REPR.sub("[内部类名已省略]", text)
    text = _FILE_PATH.sub("[路径已省略]", text)
    text = _SQL.sub("[SQL 已省略]", text)
    return _INTERNAL_MODULE.sub("[内部模块已省略]", text)


def _mask_failure_value(value: Any) -> Any:
    if value is None or value == "":
        return value
    if isinstance(value, (list, tuple)):
        return [_FAILURE_PLACEHOLDER for _ in value]
    return _FAILURE_PLACEHOLDER


def public_decision_reasoning(action: Any, reasoning: Any) -> Any:
    """单条决策 reasoning 的对外口径（结构化诊断与 run_report 渲染共用）。

    失败态（reflect/review 服务异常等）的 reasoning 可能含任意 ``str(e)``，
    整体替换为占位符；其余做内部特征脱敏。
    """
    if not isinstance(reasoning, str):
        return public_diagnostic_content(reasoning)
    if str(action or "").strip().lower() in _FAILURE_DECISION_ACTIONS:
        return _FAILURE_PLACEHOLDER
    return _redact_text(reasoning)


def _public_decision(item: Any) -> Any:
    """决策记录：失败态的 reasoning 整体替换；其余做内部特征脱敏。"""
    if not isinstance(item, dict):
        return public_diagnostic_content(item)
    out = dict(item)
    out["reasoning"] = public_decision_reasoning(out.get("action"), out.get("reasoning"))
    return out


def public_diagnostic_content(value: Any) -> Any:
    """诊断类事件内容（run_report 文本 / execution_context 结构）的脱敏。

    保留整体结构与可观测字段（token 数、步骤状态、计划 id 等）；失败/异常
    细节替换为占位符，其余字符串做内部特征脱敏。
    """
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _FAILURE_KEYS:
                out[key] = _mask_failure_value(item)
            elif key == "decisions" and isinstance(item, (list, tuple)):
                out[key] = [_public_decision(decision) for decision in item]
            else:
                out[key] = public_diagnostic_content(item)
        return out
    if isinstance(value, (list, tuple)):
        return [public_diagnostic_content(item) for item in value]
    return value


def public_agent_event_data(event: Any) -> dict[str, Any]:
    """``AgentEvent.to_sse_dict()`` 的对外口径包装（/agent 系列 SSE 出口）。"""
    data = event.to_sse_dict()
    event_type = data.get("type", "")
    if event_type in ("error", "tool_error"):
        data["content"] = public_agent_event_content(
            event_type, data.get("content"), data.get("tool_name", "")
        )
    elif event_type in ("run_report", "execution_context", "execution_step_result"):
        data["content"] = public_diagnostic_content(data.get("content"))
    elif event_type == "thinking":
        # thinking 同时携带重试上下文：content 与 retry_hint 都可能内嵌
        # 上一条 tool_error/error 的异常原文（executor 重试路径）。
        data["content"] = public_diagnostic_content(data.get("content"))
        if data.get("retry_hint"):
            data["retry_hint"] = _redact_text(str(data["retry_hint"]))
    return data


# ── JSON-RPC 错误码（issue #480 §5.4）────────────────────────────────────────
# 标准码不得改（-32600/-32601/-32602/-32603）；应用码按 issue 表格固定。
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_AUTHENTICATION_ERROR = -32001
JSONRPC_AUTHORIZATION_ERROR = -32003
JSONRPC_NOT_FOUND = -32004
JSONRPC_CONFLICT = -32009
JSONRPC_RATE_LIMIT_EXCEEDED = -32029
JSONRPC_SERVICE_UNAVAILABLE = -32050


def rpc_error_from_exception(exc: BaseException) -> tuple[int, str, Any]:
    """领域异常 → (JSON-RPC code, 对外 message, data)。

    未知异常一律 ``-32603`` + 通用文案：堆栈/内部细节只进服务端日志。
    """
    from app.exceptions import (
        AuthenticationError,
        AuthorizationError,
        ConflictError,
        NotFoundError,
        RateLimitError,
        ServiceUnavailableError,
        ValidationError,
    )

    if isinstance(exc, ValidationError):
        return JSONRPC_INVALID_PARAMS, exc.message, None
    if isinstance(exc, AuthenticationError):
        return JSONRPC_AUTHENTICATION_ERROR, exc.message, None
    if isinstance(exc, AuthorizationError):
        return JSONRPC_AUTHORIZATION_ERROR, exc.message, None
    if isinstance(exc, NotFoundError):
        return JSONRPC_NOT_FOUND, exc.message, None
    if isinstance(exc, ConflictError):
        return JSONRPC_CONFLICT, exc.message, None
    if isinstance(exc, RateLimitError):
        return JSONRPC_RATE_LIMIT_EXCEEDED, exc.message, None
    if isinstance(exc, ServiceUnavailableError):
        return JSONRPC_SERVICE_UNAVAILABLE, exc.message, None
    return JSONRPC_INTERNAL_ERROR, GENERIC_SERVER_ERROR, None
