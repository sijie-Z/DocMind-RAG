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
``LLM_CALL_LOG_PER_RUN=true`` 时按 run 分文件（``llm_calls-<task_id>.jsonl``）。
``task_id`` 进文件名前走白名单校验——非法值绝不拼进路径（对齐
``exec_context._replay_file_for`` 的 path-injection 防线）。

可观测性写入绝不影响主流程：所有异常在 :func:`append_llm_call_record`
内部吞掉并 debug 留痕，调用方永远拿到 bool。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "logs/llm_calls.jsonl"

# task_id 进文件名前的白名单（与 exec_context._SAFE_FILE_STEM 同口径）。
_SAFE_FILE_STEM = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

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


def hash_prompt(prompt: Any) -> str | None:
    """对提示词做确定性摘要（sha256），只返回摘要，不返回正文。

    - ``prompt=None`` → ``None``（调用点拿不到提示词时如实留空，不伪造）；
    - list/dict（OpenAI messages 等）→ 规范 JSON（键排序、紧凑分隔符）后求哈希；
    - 其它不可序列化形态 → ``str()`` 后求哈希（仍是摘要，不含正文）。
    """
    if prompt is None:
        return None
    if isinstance(prompt, str):
        payload = prompt
    else:
        try:
            payload = json.dumps(prompt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            payload = str(prompt)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
) -> dict[str, Any]:
    """组装一行记录。键集固定为 10 个（issue #510 规格），不夹带正文。"""
    if prompt_hash is None:
        prompt_hash = hash_prompt(prompt)
    # 错误文本防线：先替换本次提示词正文片段（provider 报错可能回显输入），
    # 再截断到 500 字符。issue #510 验收：记录不得含 prompt/completion 正文。
    error_text = _text_or_none(error)
    if error_text is not None:
        error_text = _redact_prompt_fragments(error_text, prompt)[:500]
    return {
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


def resolve_log_path(task_id: str | None = None) -> Path | None:
    """按配置解析落盘路径；总开关关闭时返回 None。

    - 默认单文件 ``LLM_CALL_LOG_FILE``；
    - ``LLM_CALL_LOG_PER_RUN=true`` 且 task_id 通过白名单 → 同目录
      ``<stem>-<task_id><suffix>``；task_id 非法则退回单文件（拒绝拼接）。
    """
    settings = _settings()
    if not bool(getattr(settings, "LLM_CALL_LOG_ENABLED", True)):
        return None
    base = Path(getattr(settings, "LLM_CALL_LOG_FILE", DEFAULT_LOG_FILE) or DEFAULT_LOG_FILE)
    per_run = bool(getattr(settings, "LLM_CALL_LOG_PER_RUN", False))
    if per_run and task_id is not None:
        raw = str(task_id)
        if _SAFE_FILE_STEM.fullmatch(raw):
            return base.with_name(f"{base.stem}-{raw}{base.suffix}")
    return base


def append_llm_call_record(record: dict[str, Any], *, path: str | Path | None = None) -> bool:
    """追加一行 JSONL。成功 True；任何失败 False（绝不向调用方抛异常）。

    ``path`` 仅用于测试/显式覆盖；为 None 时按 :func:`resolve_log_path` 解析
    （配置总开关对两种情况都生效）。
    """
    settings = _settings()
    if not bool(getattr(settings, "LLM_CALL_LOG_ENABLED", True)):
        return False
    try:
        target = Path(path) if path is not None else resolve_log_path(record.get("task_id"))
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
