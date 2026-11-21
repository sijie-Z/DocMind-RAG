"""LLM 调用级 JSONL 记录（issue #510 第一步）。

每次 LLM 调用追加一行结构化记录（append-only JSONL）：

    {"ts","task_id","request_id","model","tokens_in","tokens_out",
     "latency_ms","prompt_hash","tool_calls","error"}

**只写元数据，绝不写 prompt / completion 正文**（正文可能含 PII / 密钥，
且体积不可控）。需要还原提示词时用时序 + ``prompt_hash`` 与调用点日志对账。

ID 语义（issue #510 第二步，不做假的"统一 trace_id"）：

- ``task_id``     —— 一次 Agent run，与 ``agent_tasks.id`` 同源
  （``ExecutionContext.task_id``；SSE / RunReport 同值）；
- ``request_id``  —— 一次 HTTP 请求，与 ``X-Request-ID`` 同源
  （``app.core.request_id`` 的 contextvar；WS 无此头，如实为 null）。

写入路径可配置（``LLM_CALL_LOG_FILE``，默认 ``logs/llm_calls.jsonl``）；
``LLM_CALL_LOG_PER_RUN=true`` 时按 run 分文件
（``llm_calls-run-<sha256(task_id)[:16]>.jsonl``）——task_id **绝不**出现在
文件路径里，只取十六进制摘要，从构造上消除路径注入。

落盘路径只来自配置，不提供调用方传路径的入口（避免请求级值进入文件路径）。

可观测性写入绝不影响主流程：所有异常在 :func:`append_llm_call_record`
内部吞掉并 debug 留痕，调用方永远拿到 bool。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.log_safety import log_safe

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "logs/llm_calls.jsonl"

# 同进程内并发 append 的互斥：一次 open/write/close 落一整行。跨进程依赖
# O_APPEND 的原子性（单行远小于 PIPE_BUF/页大小）。
_append_lock = threading.Lock()


def _settings():
    """惰性取 settings（避免 import 期加载 .env；测试可 monkeypatch 字段）。"""
    from app.core.config import settings

    return settings


def _utc_now_iso() -> str:
    """UTC ISO-8601（毫秒精度，带 +00:00 偏移）——机器可解析，勿用本地时间。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _prompt_fragments(prompt: Any) -> list[str]:
    """收集提示词里的正文片段（仅用于错误文本脱敏，绝不落盘）。"""
    if isinstance(prompt, str):
        return [prompt] if len(prompt) >= 8 else []
    fragments: list[str] = []
    if isinstance(prompt, (list, tuple)):
        for item in prompt:
            fragments.extend(_prompt_fragments(item))
    elif isinstance(prompt, dict):
        for value in prompt.values():
            if isinstance(value, str):
                if len(value) >= 8:
                    fragments.append(value)
            elif isinstance(value, (list, tuple, dict)):
                fragments.extend(_prompt_fragments(value))
    return fragments


def _redact_prompt_fragments(text: str, prompt: Any) -> str:
    """把错误文本中出现的本次提示词正文片段替换为占位符。

    部分 SDK/provider 的报错会回显输入（如 pydantic 校验错误带 `input_value=`）。
    仅靠截断不能保证"不写正文"；这里对已知片段做精确替换（长片段优先），
    并覆盖 JSON/repr 转义形态（换行被转成 ``\\n`` 的多行文档正文）。
    拿不到提示词时（None）不做处理、也不臆造。
    """
    variants: set[str] = set()
    for fragment in _prompt_fragments(prompt):
        variants.add(fragment)
        for ensure_ascii in (False, True):
            try:
                # json.dumps 的转义形态 ≈ repr / provider 回显里的形态
                variants.add(json.dumps(fragment, ensure_ascii=ensure_ascii)[1:-1])
            except (TypeError, ValueError):  # pragma: no cover - 字符串必然可序列化
                continue
    for variant in sorted(variants, key=len, reverse=True):
        if variant and variant in text:
            text = text.replace(variant, "[redacted]")
    return text


def _without_surrogates(value: Any) -> str:
    """把孤立代理字符（U+D800–U+DFFF）替换为 U+FFFD。

    ``system_prompt_override`` 等字段可经 /agent/config 写入 Redis，是用户可控
    输入；JSON 反序列化允许出现孤立代理（如 ``"\\ud800"``），而
    ``str.encode("utf-8")`` 对它们必然抛 UnicodeEncodeError。任何要落盘/求哈希
    的文本都先过这里——用 U+FFFD 替换而不是抛错，保持摘要确定且不影响主流程。
    """
    text = value if isinstance(value, str) else str(value)
    return "".join("�" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)


def hash_prompt(prompt: Any) -> str | None:
    """对提示词做确定性摘要（sha256），只返回摘要，不返回正文。

    - ``prompt=None`` → ``None``（调用点拿不到提示词时如实留空，不伪造）；
    - list/dict（OpenAI messages 等）→ 规范 JSON（键排序、紧凑分隔符）后求哈希；
    - 其它不可序列化形态 → ``str()`` 后求哈希（仍是摘要，不含正文）；
    - 孤立代理字符按 U+FFFD 归一后再编码：用户可控输入不得让求哈希抛错。
    """
    if prompt is None:
        return None
    if isinstance(prompt, str):
        payload = _without_surrogates(prompt)
    else:
        try:
            payload = json.dumps(prompt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            payload = str(prompt)
        # ensure_ascii=False 会把原始代理字符原样带进 payload；统一归一后再编码。
        payload = _without_surrogates(payload)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


_OPTIONAL_TRACE_FIELDS = (
    "span_id",
    "parent_span_id",
    "span_type",
    "start_time",
    "end_time",
)

# 提示词版本/实验标签：与层级 trace 相同的"可选键"契约（旧调用点键集不变）。
# 只存标量标识，绝不存模板 name/description/content 等自由文本（可能含提示词正文）。
PROMPT_METADATA_FIELDS = (
    "prompt_id",
    "prompt_version",
    "prompt_label",
    "experiment_id",
)


def _optional_fields(
    record: dict[str, Any], names: tuple[str, ...], values: dict[str, Any]
) -> None:
    """只在调用点给出非 None、非空字符串的值时写出可选字段。

    ``None`` / ``""`` 表示"未提供"，不写出该键——旧调用点的记录键集保持不变。
    所有值统一字符串化（与 TokenUsageRecord.prompt_version 的 String 列同口径），
    不解析、不生成，也不接受正文内容。注意 ``is not None`` 判定保留 version=0。
    """
    for key in names:
        value = values.get(key)
        if value is not None and value != "":
            record[key] = _text_or_none(value)


def _optional_trace_fields(record: dict[str, Any], values: dict[str, Any]) -> None:
    """只在调用点显式传入时加入层级 trace 字段（兼容旧调用点）。"""
    _optional_fields(record, _OPTIONAL_TRACE_FIELDS, values)


def _safe_attr(obj: Any, name: str) -> Any:
    """读取已知标量属性；属性访问抛错（如 ORM 已 detach）时按缺失处理。"""
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 — 观测层不得把异常抛回业务主流程
        return None


def _safe_label(value: Any, *, max_len: int = 64) -> str | None:
    """把 label/experiment 收敛为安全标量标识。

    只接受 ``str`` / 非 ``bool`` 的 ``int``（大整数也允许）：容器、对象、bool
    一律拒绝——绝不把 ``str(SimpleNamespace(...))`` 这类对象内容写进 JSONL。
    控制字符（含 Unicode C1）用仓库既有 :func:`log_safe` 转义，再限长；空串
    视为"未提供"。注意不把用户自由文本当作版本来源，label 只是窄化后的标识。
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    # 孤立代理先归一（否则落盘/控制台编码会抛错），再转义 C1 与 C0/DEL。
    escaped = "".join(
        ch if not 0x80 <= ord(ch) <= 0x9F else f"\\x{ord(ch):02x}"
        for ch in _without_surrogates(value)
    )
    cleaned = log_safe(escaped, max_len=max_len)
    return cleaned or None


def resolve_prompt_metadata(
    template: Any = None,
    *,
    prompt_label: Any = None,
    experiment_id: Any = None,
) -> dict[str, str]:
    """从 PromptTemplate（或等价对象）解析**纯元数据**，不查库、不读正文。

    - ``prompt_id`` / ``prompt_version`` 只取模板的 ``id`` / ``version``
      整数字段（``bool`` 与空串视为缺失）；**不读** ``name`` / ``description``
      / ``content``：这些是用户自由文本或提示词正文，不得进入日志与报告；
    - ``prompt_label`` / ``experiment_id`` 只来自调用方的 str/非 bool int 标识，
      经控制字符转义与限长；模板没有 label/experiment 概念时不臆造；
    - 输出全部为 ``str``，与 :func:`build_llm_call_record` 的落盘口径一致。

    这是一个纯函数：不改 template，不做 I/O，任何属性访问异常都按"未提供"降级。
    """
    result: dict[str, str] = {}
    if template is not None:
        for key, attr in (("prompt_id", "id"), ("prompt_version", "version")):
            value = _safe_attr(template, attr)
            if type(value) is int:  # 排除 bool / 字符串 / MagicMock 等非真实整数
                result[key] = str(value)
    for key, value in (("prompt_label", prompt_label), ("experiment_id", experiment_id)):
        cleaned = _safe_label(value)
        if cleaned is not None:
            result[key] = cleaned
    return result


def derive_run_prompt_metadata(
    config: Any = None,
    *,
    template: Any = None,
    prompt_text: str | None = None,
    fallback_prompt: str | None = None,
    prompt_label: Any = None,
    experiment_id: Any = None,
) -> dict[str, str]:
    """为一个真实 run 派生可选的版本标识（纯函数，无 DB / 无 I/O）。

    优先级：显式 ``template``（真实 PromptTemplate 行）> ``prompt_text`` >
    ``config.system_prompt_override`` > ``fallback_prompt``（模块常量）。

    - 有 DB 模板时只取 ``id`` / ``version``（:func:`resolve_prompt_metadata`），
      不做任何正文哈希；
    - 无模板但有可用提示词来源时，生成**稳定**的 ``prompt_id``（配置身份 +
      提示词的摘要）与 ``prompt_version``（提示词摘要）——两者都是摘要，
      绝不含提示词正文；``prompt_label`` 缺省为 ``agent-config``；
    - 全部来源缺失时返回空字典：旧 run 的 JSONL / 报告键集保持不变。

    观测层的属性访问失败一律按缺失降级，绝不把异常抛回主流程。
    """
    try:
        return _derive_run_prompt_metadata(
            config,
            template=template,
            prompt_text=prompt_text,
            fallback_prompt=fallback_prompt,
            prompt_label=prompt_label,
            experiment_id=experiment_id,
        )
    except Exception:  # noqa: BLE001 — 观测派生失败不得影响 run（字段留空）
        logger.debug("prompt metadata derivation skipped", exc_info=True)
        return {}


def _derive_run_prompt_metadata(
    config: Any = None,
    *,
    template: Any = None,
    prompt_text: str | None = None,
    fallback_prompt: str | None = None,
    prompt_label: Any = None,
    experiment_id: Any = None,
) -> dict[str, str]:
    """:func:`derive_run_prompt_metadata` 的实现体（异常由外层统一兜底）。"""
    meta = resolve_prompt_metadata(template, prompt_label=prompt_label, experiment_id=experiment_id)
    if meta.get("prompt_id"):
        return meta

    source = prompt_text
    if source is None and config is not None:
        raw = _safe_attr(config, "system_prompt_override")
        source = raw if isinstance(raw, str) and raw else None
    if not source:
        source = fallback_prompt
    if not isinstance(source, str) or not source:
        return meta

    digest = hash_prompt(source) or ""
    version = digest.removeprefix("sha256:")[:16]
    agent_id = _safe_attr(config, "agent_id")
    model = _safe_attr(config, "model")
    identity = {
        "agent_id": agent_id if isinstance(agent_id, str) else "",
        "model": model if isinstance(model, str) else "",
        "prompt": source,
    }
    identity_digest = hash_prompt(identity) or ""
    meta["prompt_id"] = "config-" + identity_digest.removeprefix("sha256:")[:16]
    meta["prompt_version"] = version
    cleaned_label = _safe_label(prompt_label)
    meta["prompt_label"] = cleaned_label or "agent-config"
    if experiment_id is not None:
        cleaned_experiment = _safe_label(experiment_id)
        if cleaned_experiment is not None:
            meta["experiment_id"] = cleaned_experiment
    return meta


def build_llm_call_record(
    *,
    task_id: str | None,
    request_id: str | None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: float | int | None = None,
    prompt: Any = None,
    prompt_hash: str | None = None,
    tool_calls: int = 0,
    error: Any = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    span_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    prompt_id: str | int | None = None,
    prompt_version: str | int | None = None,
    prompt_label: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """组装一行记录。默认键集固定为 10 个；可选字段只在传入时加入。"""
    if prompt_hash is None:
        prompt_hash = hash_prompt(prompt)
    # 错误文本防线：先替换本次提示词正文片段（provider 报错可能回显输入），
    # 再截断到 500 字符。issue #510 验收：记录不得含 prompt/completion 正文。
    error_text = _text_or_none(error)
    if error_text is not None:
        error_text = _redact_prompt_fragments(error_text, prompt)[:500]
    record = {
        "ts": _utc_now_iso(),
        "task_id": _text_or_none(task_id),
        "request_id": _text_or_none(request_id),
        "model": _text_or_none(model) or "unknown",
        "tokens_in": _as_int(tokens_in),
        "tokens_out": _as_int(tokens_out),
        "latency_ms": round(float(latency_ms), 1) if latency_ms is not None else None,
        "prompt_hash": prompt_hash,
        "tool_calls": _as_int(tool_calls),
        "error": error_text,
    }
    _optional_trace_fields(
        record,
        {
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "span_type": span_type,
            "start_time": start_time,
            "end_time": end_time,
        },
    )
    _optional_fields(
        record,
        PROMPT_METADATA_FIELDS,
        {
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "prompt_label": prompt_label,
            "experiment_id": experiment_id,
        },
    )
    return record


def resolve_log_path(task_id: str | None = None) -> Path | None:
    """按配置解析落盘路径；总开关关闭时返回 None。

    - 默认单文件 ``LLM_CALL_LOG_FILE``；
    - ``LLM_CALL_LOG_PER_RUN=true`` 且提供 task_id → 同目录
      ``<stem>-run-<sha256(task_id)[:16]><suffix>``。

    task_id 不参与路径拼接：只取 SHA-256 摘要前 16 位（纯 ``[0-9a-f]``）。
    这样路径分量与用户输入无字符级关联（CodeQL path-injection 的结构性修法），
    也不依赖任何"白名单正则被识别为 sanitizer"的假设。
    """
    settings = _settings()
    if not bool(getattr(settings, "LLM_CALL_LOG_ENABLED", True)):
        return None
    base = Path(getattr(settings, "LLM_CALL_LOG_FILE", DEFAULT_LOG_FILE) or DEFAULT_LOG_FILE)
    per_run = bool(getattr(settings, "LLM_CALL_LOG_PER_RUN", False))
    if per_run and task_id is not None:
        digest = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:16]
        return base.with_name(f"{base.stem}-run-{digest}{base.suffix}")
    return base


def append_llm_call_record(record: dict[str, Any]) -> bool:
    """追加一行 JSONL。成功 True；任何失败 False（绝不向调用方抛异常）。

    落盘路径**只来自配置**（:func:`resolve_log_path`）——本函数不接受调用方
    传入路径，避免把请求级/用户可控值带进文件路径。需要改落点（如测试）时
    通过配置 ``LLM_CALL_LOG_FILE`` 指定目录。
    """
    settings = _settings()
    if not bool(getattr(settings, "LLM_CALL_LOG_ENABLED", True)):
        return False
    try:
        target = resolve_log_path(record.get("task_id"))
        if target is None:
            return False
        if target.parent and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _append_lock:
            # newline="\n"：Windows 文本模式默认写 \r\n；JSONL 跨平台保持 LF。
            with open(target, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 — 观测写入不得影响主流程
        logger.debug("LLM call JSONL write skipped (%s)", type(exc).__name__)
        return False
