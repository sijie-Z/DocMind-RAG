"""OpenAI 兼容聊天入口的业务编排（批次十五）。

职责边界：
- 把 OpenAI ``messages`` 映射为 DocMind RAG 的「本轮 query / history / system」；
- 把 ``model`` 映射为组织知识库范围（``docmind`` / ``docmind:<kb_id>``），
  跨组织与不存在的 KB 统一 404，不泄露存在性；
- 复用既有检索（``rag_service.search_knowledge_base``）与生成
  （``rag_service.chat_stream``），不复制会话落库/SSE 逻辑；
- 生成结束后记 token 用量（``source="openai_compat"``）与无正文审计。

不在这里做的事：认证/scope、配额检查、HTTP/SSE 编码——那些在
``app.api.v1.endpoints.openai_compat``，避免业务层依赖 FastAPI。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions import NotFoundError, ValidationError
from app.models.knowledge_base import KnowledgeBase
from app.schemas.openai_compat import OpenAIChatMessage
from app.services.audit_service import audit_service
from app.services.masking_service import masking_service
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

SUPPORTED_MODEL = "docmind"
MODEL_PREFIX = "docmind:"
MAX_PUBLIC_CITATIONS = 8
_DEFAULT_RETRIEVAL_TOP_K = 5
# masking_service 的占位符形状：[PHONE_1] / [ID_CARD_12] / [EMAIL_3]。
# 流式只扣留形似该形状的未闭合后缀，普通 `[` 不得阻塞正文。
_PLACEHOLDER_TAIL_RE = re.compile(r"\[[A-Z_]{0,16}\d{0,4}")


@dataclass(frozen=True)
class ChatTurn:
    """一次 OpenAI 请求映射出的 RAG 输入。"""

    query: str
    history: list[dict[str, str]]
    system_prompt: str | None


@dataclass(frozen=True)
class ModelTarget:
    """``model`` 字段解析结果；``knowledge_base_id=None`` 表示组织全范围。"""

    requested_model: str
    knowledge_base_id: str | None


def parse_chat_messages(messages: list[OpenAIChatMessage]) -> ChatTurn:
    """把 OpenAI ``messages[]`` 映射为 (query, history, system_prompt)。

    规则（本批 v1，文档化而非静默猜测）：
    - ``system`` 消息可出现在任意位置，全部按出现顺序拼接后作为
      ``system_prompt_override``；
    - 最后一条非 system 消息必须是 ``user``，它作为本轮 query；
    - 它之前的 user/assistant 消息按原顺序作为 history；
    - 不允许最后一条 user 之后还有消息（不支持 assistant prefill）；
    - 空 messages、无 user、content 非字符串、未知角色一律 400。
    """
    if not messages:
        raise ValidationError("messages 不能为空", error_code="INVALID_MESSAGES")

    system_parts: list[str] = []
    non_system: list[tuple[int, str, str]] = []
    for index, message in enumerate(messages):
        role = message.role if isinstance(message.role, str) else ""
        content = message.content
        if role == "system":
            if not isinstance(content, str):
                raise ValidationError(
                    "system 消息的 content 必须是字符串", error_code="INVALID_MESSAGES"
                )
            system_parts.append(content)
            continue
        if role not in ("user", "assistant"):
            raise ValidationError(
                f"不支持的消息角色: {role or '<empty>'}（仅支持 system/user/assistant）",
                error_code="INVALID_MESSAGES",
            )
        if not isinstance(content, str):
            # 数组/多模态 content 不在本批范围；明确拒绝，绝不把对象当成正文。
            raise ValidationError(
                f"{role} 消息的 content 必须是字符串（不支持多模态内容）",
                error_code="INVALID_MESSAGES",
            )
        if role == "user" and not content.strip():
            raise ValidationError("user 消息内容不能为空", error_code="INVALID_MESSAGES")
        non_system.append((index, role, content))

    if not non_system:
        raise ValidationError("messages 中至少需要一条 user 消息", error_code="INVALID_MESSAGES")

    last_index, last_role, last_content = non_system[-1]
    if last_role != "user":
        raise ValidationError(
            "最后一条非 system 消息必须是 user（不支持 assistant prefill）",
            error_code="INVALID_MESSAGES",
        )
    if last_index != len(messages) - 1:
        # non_system 已排除 system；若最后一条消息是 system，也按「user 之后
        # 还有消息」处理——保持规则单一，不猜测客户端意图。
        raise ValidationError(
            "最后一条消息必须是 user（system 请放在 user 之前）",
            error_code="INVALID_MESSAGES",
        )

    history = [{"role": role, "content": content} for _, role, content in non_system[:-1]]
    system_prompt = "\n\n".join(part for part in system_parts if part) or None
    return ChatTurn(query=last_content, history=history, system_prompt=system_prompt)


async def resolve_model_target(db: AsyncSession, model: str, organization_id: int) -> ModelTarget:
    """解析 ``docmind`` / ``docmind:<kb_id>``；其它模型 400。

    KB 校验同时按 id 与 organization_id 过滤：跨组织与不存在的 KB 得到
    完全相同的 404 文案，调用方无法据此枚举他人知识库。
    """
    if model == SUPPORTED_MODEL:
        return ModelTarget(requested_model=model, knowledge_base_id=None)

    if model.startswith(MODEL_PREFIX):
        kb_id = model[len(MODEL_PREFIX) :].strip()
        if not kb_id:
            raise ValidationError(
                "模型格式应为 docmind 或 docmind:<knowledge_base_id>",
                error_code="MODEL_NOT_SUPPORTED",
            )
        exists = (
            await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.id == kb_id,
                    KnowledgeBase.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            # 统一安全文案：不存在 / 跨组织不可区分。
            raise NotFoundError("知识库不存在或无权访问", error_code="KNOWLEDGE_BASE_NOT_FOUND")
        return ModelTarget(requested_model=model, knowledge_base_id=kb_id)

    raise ValidationError(
        f"不支持的模型: {model}；可用: docmind 或 docmind:<knowledge_base_id>",
        error_code="MODEL_NOT_SUPPORTED",
    )


def _public_citation_id(raw_id: Any) -> str:
    """内部 document/chunk id → 不可反查的稳定公开引用 id。

    正确性评审明确要求引用**不得包含 document_id**：直接用原 id 会把内部
    资源标识暴露给外部 OpenAI 客户端。这里用 SHA-256 截断派生 ``cite_...``，
    对同一文档稳定、可去重，但不能反推内部 id。
    """
    digest = hashlib.sha256(str(raw_id).encode("utf-8")).hexdigest()
    return f"cite_{digest[:16]}"


def build_public_citations(context_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造公开引用扩展：只含公开引用 id / 标题 / 分数。

    刻意不暴露 document_id、chunk_id、file_path、organization_id、
    uploaded_by、正文 chunk 等内部字段；标准 OpenAI 客户端会忽略该扩展字段。
    """
    citations: dict[str, dict[str, Any]] = {}
    for result in context_results or []:
        if not isinstance(result, dict):
            continue
        internal_id = result.get("document_id") or result.get("chunk_id")
        if not internal_id:
            continue
        title = result.get("filename") or result.get("title") or "未知文档"
        raw_score = result.get("relevance_score")
        if raw_score is None:
            raw_score = result.get("score")
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        key = _public_citation_id(internal_id)
        existing = citations.get(key)
        if existing is None:
            if len(citations) >= MAX_PUBLIC_CITATIONS:
                break
            citations[key] = {"id": key, "title": str(title), "score": score}
        elif score is not None and (existing["score"] is None or score > existing["score"]):
            existing["score"] = score
    return list(citations.values())


def effective_model(org_policy: Any) -> str:
    """本次生成实际会用的模型名（组织默认 > 进程默认），用于记账。"""
    default_model = getattr(org_policy, "default_model", None)
    if isinstance(default_model, str) and default_model:
        return default_model
    if settings.ENABLE_LOCAL_LLM:
        return settings.LOCAL_LLM_MODEL
    return settings.DEEPSEEK_MODEL


def retrieval_top_k(org_policy: Any) -> int:
    """与既有「通用聊天」同口径：组织覆盖 > 5（无文档绑定时）。"""
    value = getattr(org_policy, "retrieval_top_k", None)
    if isinstance(value, int) and value > 0:
        return value
    return _DEFAULT_RETRIEVAL_TOP_K


async def retrieve_context(
    *,
    organization_id: int,
    query: str,
    user_id: int,
    knowledge_base_id: str | None,
    top_k: int,
) -> list[dict[str, Any]]:
    """检索；KB 范围与私有文档 ACL 都由既有 pipeline 统一执行。"""
    results, _debug = await rag_service.search_knowledge_base(
        query,
        organization_id,
        top_k,
        knowledge_base_id=knowledge_base_id,
        viewer_user_id=user_id,
    )
    return results or []


async def collect_completion(
    *,
    query: str,
    context_results: list[dict[str, Any]],
    history: list[dict[str, str]],
    system_prompt: str | None,
    model: str,
    enable_masking: bool,
    extra_mask_mapping: dict[str, str] | None = None,
) -> str:
    """非流式：消费完整个 chunk 流，还原「引用型」PII 占位符后返回正文。

    ``extra_mask_mapping``：调用方在进入 pipeline 前自行掩码的额外文本
    （如公开聊天的客户端 history）映射；默认 None 时行为与旧调用完全一致。
    """
    mask_mapping: dict[str, str] = dict(extra_mask_mapping or {})
    # pipeline 会在流开始时 clear() 再写入 context/query 映射；用独立 sink
    # 承接，避免把调用方的 extra（如公开聊天 history）映射清掉。
    pipeline_mask_mapping: dict[str, str] = {}
    parts: list[str] = []
    stream = rag_service.chat_stream(
        query,
        context_results,
        history,
        system_prompt_override=system_prompt,
        enable_masking=enable_masking,
        mask_mapping_sink=pipeline_mask_mapping,
        model_override=model,
        # 显式传 client_override：pipeline 只有在 client_override 非空时才把
        # 上游异常转成 UserLLMRequestError；否则会把「系统处理异常」当正文
        # yield 给调用方（HTTP 200 假成功）。这里传入的正是全局客户端，行为
        # 与不传一致的正常路径不变。
        client_override=getattr(rag_service, "openai_client", None),
        raise_user_errors=True,
    )
    try:
        async for chunk in stream:
            parts.append(chunk)
    finally:
        # 上游失败/请求取消时关闭生成器；shield 保证 close 的 await 不被
        # 当前 cancel scope 立刻打断。
        with anyio.CancelScope(shield=True):
            await stream.aclose()
    mask_mapping.update(pipeline_mask_mapping)
    answer = "".join(parts)
    return masking_service.unmask_text(answer, mask_mapping)


def _split_partial_placeholder(text: str) -> tuple[str, str]:
    """把「末尾可能是半个 PII 占位符」的后缀留到下一 chunk。

    占位符形如 ``[PHONE_1]``。只有**形似占位符且长度有界**的后缀才回退，
    避免回答里的普通未闭合 ``[``（JSON 数组、区间、Markdown 链接）把后续
    整段正文无限期扣留：只认 ``[A-Z_]{0,16}\\d{0,4}`` 且总长 ≤ 24。
    """
    start = text.rfind("[")
    if start == -1 or "]" in text[start:]:
        return text, ""
    tail = text[start:]
    if len(tail) <= 24 and _PLACEHOLDER_TAIL_RE.fullmatch(tail):
        return text[:start], tail
    return text, ""


async def iter_completion_chunks(
    *,
    query: str,
    context_results: list[dict[str, Any]],
    history: list[dict[str, str]],
    system_prompt: str | None,
    model: str,
    enable_masking: bool,
    extra_mask_mapping: dict[str, str] | None = None,
) -> AsyncGenerator[str, None]:
    """流式：逐 chunk 产出已还原引用型占位符的正文。

    调用方断连/取消时在 ``finally`` 中显式 ``aclose`` 上游生成器，并用
    ``anyio.CancelScope(shield=True)`` 保证 close 的 await 不被已经取消的
    cancel scope 立刻打断，从而真正取消底层 LLM 流（不会双份生成）。
    ``extra_mask_mapping`` 语义同 :func:`collect_completion`。
    """
    mask_mapping: dict[str, str] = dict(extra_mask_mapping or {})
    # 同 collect_completion：独立 sink 承接 pipeline 的 clear()+写入。
    pipeline_mask_mapping: dict[str, str] = {}
    pending = ""
    stream = rag_service.chat_stream(
        query,
        context_results,
        history,
        system_prompt_override=system_prompt,
        enable_masking=enable_masking,
        mask_mapping_sink=pipeline_mask_mapping,
        model_override=model,
        # 同 collect_completion：非空 client_override 才会把上游异常转成
        # UserLLMRequestError，避免把错误文案当回答流给客户端。
        client_override=getattr(rag_service, "openai_client", None),
        raise_user_errors=True,
    )
    try:
        async for chunk in stream:
            if not chunk:
                continue
            # pipeline 在首个 chunk 之前写入 sink；每帧合并一次（幂等、量小）。
            if pipeline_mask_mapping:
                mask_mapping.update(pipeline_mask_mapping)
            text = pending + chunk
            emit, pending = _split_partial_placeholder(text)
            if emit:
                yield masking_service.unmask_text(emit, mask_mapping)
    finally:
        with anyio.CancelScope(shield=True):
            await stream.aclose()
    if pipeline_mask_mapping:
        mask_mapping.update(pipeline_mask_mapping)
    if pending:
        yield masking_service.unmask_text(pending, mask_mapping)


def current_token_usage() -> tuple[int, int] | None:
    """本请求最近一次 LLM 调用的 (input, output)；无则 None（不编造）。"""
    usage = rag_service.get_last_token_usage()
    if not usage or len(usage) != 2:
        return None
    try:
        return int(usage[0]), int(usage[1])
    except (TypeError, ValueError):
        return None


async def record_usage(
    *,
    db: AsyncSession,
    user_id: int,
    organization_id: int,
    model: str,
    usage: tuple[int, int] | None,
    request_id: str | None = None,
) -> None:
    """写 ``source=openai_compat`` 的 token 账；失败不影响响应。"""
    if not usage:
        return
    input_tokens, output_tokens = usage
    if input_tokens <= 0 and output_tokens <= 0:
        return
    from app.api.v1.endpoints.token_usage import record_token_usage

    try:
        await record_token_usage(
            db=db,
            user_id=user_id,
            organization_id=organization_id,
            model=model,
            source="openai_compat",
            feature="openai_compat",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
        )
    except Exception as exc:  # noqa: BLE001 — 记账失败不得反噬已经生成的回答
        logger.warning("openai_compat token usage recording failed: %s", type(exc).__name__)


async def audit_completion(
    *,
    user_id: int,
    organization_id: int,
    requested_model: str,
    knowledge_base_id: str | None,
    stream: bool,
    usage: tuple[int, int] | None,
    request: Any = None,
) -> None:
    """审计只记 model/KB/stream/usage，绝不写正文或密钥。"""
    input_tokens, output_tokens = usage if usage else (0, 0)
    detail = json.dumps(
        {
            "model": requested_model,
            "knowledge_base_id": knowledge_base_id,
            "stream": bool(stream),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        ensure_ascii=False,
    )
    try:
        await audit_service.log_activity(
            user_id=user_id,
            action="openai_chat_completion",
            target_type="knowledge_base" if knowledge_base_id else "organization",
            target_id=knowledge_base_id or str(organization_id),
            detail=detail,
            request=request,
        )
    except Exception as exc:  # noqa: BLE001 — 审计失败不影响响应
        logger.warning("openai_compat audit logging failed: %s", type(exc).__name__)
