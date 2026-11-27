"""公开知识库只读检索（批次十三）：管理员发布面 + 匿名检索面。

授权模型（红线）：
- **管理面**只认组织管理员（端点层 ``require_org_admin``），所有查询同时限定
  ``organization_id``；跨组织/不存在一律 404，不回显存在性。
- **公开面**只认 token：SHA-256 摘要等值查询；禁用/撤销/过期/不存在一律同一个
  404（不区分原因）。token 明文只在创建/轮换响应返回一次，DB 只存摘要。
- 公开检索**只**查 ``document_publication`` 显式白名单：绝不回退到知识库全量、
  组织级默认检索或 ``document_acl.get_blocked_document_ids(viewer=None)`` 这类
  fail-open 路径。白名单在每次请求时重新 join ``documents`` 复核组织/知识库/
  ``INDEXED``/启用状态；检索结果返回前还会按白名单二次过滤（ES 命中不等于
  授权）。

数据口径：
- 任务书里的「status=ready」在本仓库对应唯一可检索终态
  ``DocumentStatus.INDEXED``（见 ``app/services/document_status.py``：INDEXED
  只能在校验有 chunk + embedding + 组织一致之后写入）。
- 白名单只约束「哪篇文档可被匿名检索」；``documents.visibility`` / ACL 语义
  完全不变，公开面不读取 ACL。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_rag_pipeline
from app.exceptions import NotFoundError, ValidationError
from app.models.document import Document, DocumentStatus, DocumentVisibility
from app.models.knowledge_base import KnowledgeBase
from app.models.public_kb import DocumentPublication, KbPublication
from app.services.share_link_service import new_share_token, share_token_digest

logger = logging.getLogger(__name__)

# 过期上限：公开链接是受控的对外入口，不允许变相永久公开（显式「不过期」除外）。
MAX_EXPIRY_DAYS = 90
# 单次发布的白名单上限：有界响应/有界校验成本。
MAX_DOCUMENTS_PER_PUBLICATION = 200
# 候选文档扫描上限（管理面选择器）：超过后截断并如实置 truncated。
MAX_CANDIDATES_SCAN = 500
# 公开检索输入上限。
MAX_QUERY_CHARS = 200
MAX_TOP_K = 10
DEFAULT_TOP_K = 5
# 公开响应片段上限（字符数，去 HTML 后）。
SNIPPET_MAX_CHARS = 300
TITLE_MAX_CHARS = 200
# token 长度上限：正常 token ~32 字符；超长输入直接按无效处理（不做哈希）。
MAX_TOKEN_CHARS = 128

PUBLICATION_INVALID = "发布不存在或已失效"
DOCUMENT_NOT_PUBLISHABLE = "文档不存在或不可公开"

_WHITESPACE_RE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """线性 HTML 文本抽取：跳过 script/style，不依赖可能回溯的正则。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _strip_html(value: str | None) -> str:
    """去掉 script/style 整块与标签，再折叠空白（HTMLParser，无 ReDoS）。"""
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:  # noqa: BLE001 — 畸形 HTML 也不能让公开检索 500
        return _WHITESPACE_RE.sub(" ", value or "").replace("<", " ").replace(">", " ").strip()
    return _WHITESPACE_RE.sub(" ", " ".join(parser.parts)).strip()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读回的 DateTime 可能不带时区；统一按 UTC 解释后比较。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized else None


def _clean_plain_text(value: str) -> str:
    """标题/描述是纯展示文本：去 HTML、折叠空白、去首尾空格。"""
    return _strip_html(value)


def _validate_title(title: str | None) -> str:
    cleaned = _clean_plain_text(title or "")
    if not cleaned:
        raise ValidationError("标题不能为空", field="title")
    if len(cleaned) > 100:
        raise ValidationError("标题不能超过 100 字符", field="title", max=100)
    return cleaned


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    cleaned = _clean_plain_text(description)
    if not cleaned:
        return None
    if len(cleaned) > 500:
        raise ValidationError("描述不能超过 500 字符", field="description", max=500)
    return cleaned


def _validate_expiry(expires_in_days: int | None) -> datetime | None:
    if expires_in_days is None:
        return None
    if isinstance(expires_in_days, bool) or not isinstance(expires_in_days, int):
        raise ValidationError("过期天数必须是整数", field="expires_in_days", found=expires_in_days)
    if expires_in_days < 1 or expires_in_days > MAX_EXPIRY_DAYS:
        raise ValidationError(
            f"过期天数必须在 1 到 {MAX_EXPIRY_DAYS} 之间",
            field="expires_in_days",
            min=1,
            max=MAX_EXPIRY_DAYS,
            found=expires_in_days,
        )
    return _now() + timedelta(days=expires_in_days)


def publication_is_active(publication: KbPublication) -> bool:
    """发布当前是否可公开读取：启用、未撤销、未过期。"""
    if not publication.enabled:
        return False
    if publication.revoked_at is not None:
        return False
    expires_at = _as_utc(publication.expires_at)
    return expires_at is None or expires_at > _now()


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _document_title(title: str | None, filename: str | None) -> str:
    text = _clean_plain_text(title or "")
    if not text:
        text = _clean_plain_text(filename or "")
    return text[:TITLE_MAX_CHARS] or "—"


def _sanitize_snippet(value: str | None) -> str:
    return _strip_html(value)[:SNIPPET_MAX_CHARS]


async def _get_knowledge_base(
    db: AsyncSession, *, organization_id: int, knowledge_base_id: str
) -> KnowledgeBase:
    """知识库必须属于当前组织；跨组织/不存在同形 404。"""
    kb = (
        await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError("知识库不存在")
    return kb


async def get_publication(
    db: AsyncSession, *, publication_id: str, organization_id: int
) -> KbPublication:
    """按「组织 + id」取发布行；跨组织/不存在同形 404。"""
    row = (
        await db.execute(
            select(KbPublication).where(
                KbPublication.id == publication_id,
                KbPublication.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(PUBLICATION_INVALID)
    return row


async def publication_meta(db: AsyncSession, publication: KbPublication) -> tuple[str | None, int]:
    """返回 (知识库名, 白名单行数)；知识库已删时名称为 None（发布悬空但不可召回）。"""
    kb_name = (
        await db.execute(
            select(KnowledgeBase.name).where(
                KnowledgeBase.id == publication.knowledge_base_id,
                KnowledgeBase.organization_id == publication.organization_id,
            )
        )
    ).scalar_one_or_none()
    count = (
        await db.execute(
            select(func.count())
            .select_from(DocumentPublication)
            .where(
                DocumentPublication.publication_id == publication.id,
                DocumentPublication.organization_id == publication.organization_id,
            )
        )
    ).scalar() or 0
    return kb_name, int(count)


def publication_view(
    publication: KbPublication, *, knowledge_base_name: str | None, document_count: int
) -> dict[str, Any]:
    """管理面状态投影（绝不含 token / token 摘要）。"""
    return {
        "id": publication.id,
        "knowledge_base_id": publication.knowledge_base_id,
        "knowledge_base_name": knowledge_base_name,
        "title": publication.title,
        "description": publication.description,
        "enabled": bool(publication.enabled),
        "active": publication_is_active(publication),
        "expires_at": _iso(publication.expires_at),
        "revoked_at": _iso(publication.revoked_at),
        "created_at": _iso(publication.created_at),
        "document_count": document_count,
    }


async def create_publication(
    db: AsyncSession,
    *,
    organization_id: int,
    knowledge_base_id: str,
    title: str,
    description: str | None,
    expires_in_days: int | None,
    created_by: int,
    document_ids: list[str] | None = None,
) -> tuple[KbPublication, str]:
    """创建发布；返回 ``(发布行, token 明文)``。token 只在此刻返回一次。"""
    await _get_knowledge_base(
        db, organization_id=organization_id, knowledge_base_id=knowledge_base_id
    )
    token = new_share_token()
    publication = KbPublication(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        token_hash=share_token_digest(token),
        title=_validate_title(title),
        description=_validate_description(description),
        enabled=True,
        expires_at=_validate_expiry(expires_in_days),
        created_by=created_by,
        created_at=_now(),
        revoked_at=None,
    )
    db.add(publication)
    await db.flush()
    if document_ids:
        await _apply_documents(
            db,
            publication=publication,
            document_ids=document_ids,
            mode="replace",
            created_by=created_by,
        )
        await db.flush()
    return publication, token


async def rotate_publication(
    db: AsyncSession,
    *,
    publication_id: str,
    organization_id: int,
    expires_in_days: int | None = None,
    operator_id: int,
) -> tuple[KbPublication, str]:
    """轮换 token：旧链接立即失效。撤销/启用状态保持不变，不隐式复活已撤销发布。"""
    publication = await get_publication(
        db, publication_id=publication_id, organization_id=organization_id
    )
    token = new_share_token()
    publication.token_hash = share_token_digest(token)
    if expires_in_days is not None:
        publication.expires_at = _validate_expiry(expires_in_days)
    publication.created_by = operator_id
    publication.created_at = _now()
    await db.flush()
    return publication, token


async def revoke_publication(
    db: AsyncSession, *, publication_id: str, organization_id: int, operator_id: int
) -> KbPublication:
    """撤销发布（幂等：已撤销再撤销仍成功）；公开面统一 404。"""
    publication = await get_publication(
        db, publication_id=publication_id, organization_id=organization_id
    )
    if publication.revoked_at is None:
        publication.revoked_at = _now()
        publication.created_by = operator_id
        await db.flush()
    return publication


async def set_documents(
    db: AsyncSession,
    *,
    publication_id: str,
    organization_id: int,
    document_ids: list[str],
    mode: str,
    operator_id: int,
) -> list[str]:
    """批量设置白名单（replace/add/remove）；返回最终白名单文档 id。"""
    publication = await get_publication(
        db, publication_id=publication_id, organization_id=organization_id
    )
    result = await _apply_documents(
        db,
        publication=publication,
        document_ids=document_ids,
        mode=mode,
        created_by=operator_id,
    )
    await db.flush()
    return result


async def _apply_documents(
    db: AsyncSession,
    *,
    publication: KbPublication,
    document_ids: list[str],
    mode: str,
    created_by: int,
) -> list[str]:
    """白名单变更的唯一写入口（create 与 set_documents 共用同一套校验）。"""
    if mode not in {"replace", "add", "remove"}:
        raise ValidationError("mode 只能是 replace/add/remove", field="mode")
    if not isinstance(document_ids, list):
        raise ValidationError("document_ids 必须是数组", field="document_ids")
    requested = {str(doc_id) for doc_id in document_ids if str(doc_id).strip()}
    if len(requested) > MAX_DOCUMENTS_PER_PUBLICATION:
        raise ValidationError(
            f"单次白名单最多 {MAX_DOCUMENTS_PER_PUBLICATION} 篇文档",
            field="document_ids",
            max=MAX_DOCUMENTS_PER_PUBLICATION,
        )

    existing = set(
        (
            await db.execute(
                select(DocumentPublication.document_id).where(
                    DocumentPublication.publication_id == publication.id,
                    DocumentPublication.organization_id == publication.organization_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if mode == "replace":
        target = set(requested)
    elif mode == "add":
        target = existing | requested
    else:
        target = existing - requested
    if len(target) > MAX_DOCUMENTS_PER_PUBLICATION:
        raise ValidationError(
            f"白名单最多 {MAX_DOCUMENTS_PER_PUBLICATION} 篇文档",
            field="document_ids",
            max=MAX_DOCUMENTS_PER_PUBLICATION,
        )

    if target:
        # 只允许「本组织 + 同知识库 + 已索引 + 启用」的文档，任何一条不满足
        # 都整体拒绝（不做部分写入，避免管理员误以为已全部生效）。
        valid_ids = set(
            (
                await db.execute(
                    select(Document.id).where(
                        Document.id.in_(target),
                        Document.organization_id == publication.organization_id,
                        Document.knowledge_base_id == publication.knowledge_base_id,
                        Document.status == DocumentStatus.INDEXED,
                        Document.is_enabled.is_(True),
                        # 公开面只允许组织可见文档；private 是 ACL 限定给特定用户，
                        # 发布到匿名 token 等于绕过文档 ACL。
                        Document.visibility == DocumentVisibility.ORGANIZATION,
                    )
                )
            )
            .scalars()
            .all()
        )
        if valid_ids != target:
            raise NotFoundError(DOCUMENT_NOT_PUBLISHABLE)

    # 全量替换是最简单的原子语义：add/remove 也归一到「目标集合」后重写。
    await db.execute(
        delete(DocumentPublication).where(
            DocumentPublication.publication_id == publication.id,
            DocumentPublication.organization_id == publication.organization_id,
        )
    )
    for document_id in sorted(target):
        db.add(
            DocumentPublication(
                id=str(uuid.uuid4()),
                organization_id=publication.organization_id,
                publication_id=publication.id,
                document_id=document_id,
                created_by=created_by,
                created_at=_now(),
            )
        )
    await db.flush()
    return sorted(target)


async def _searchable_whitelist(
    db: AsyncSession, publication: KbPublication
) -> dict[str, dict[str, str]]:
    """公开检索的唯一授权集合：白名单 ∩ 当前文档状态（组织/知识库/INDEXED/启用）。

    **绝不回退**：任何一步收窄为空都返回空集合，调用方直接返回空结果。
    """
    rows = (
        await db.execute(
            select(Document.id, Document.title, Document.filename)
            .join(DocumentPublication, DocumentPublication.document_id == Document.id)
            .where(
                DocumentPublication.publication_id == publication.id,
                DocumentPublication.organization_id == publication.organization_id,
                Document.organization_id == publication.organization_id,
                Document.knowledge_base_id == publication.knowledge_base_id,
                Document.status == DocumentStatus.INDEXED,
                Document.is_enabled.is_(True),
                # 读时复核：私有文档即使有脏白名单行也不得匿名检索。
                Document.visibility == DocumentVisibility.ORGANIZATION,
            )
        )
    ).all()
    return {
        row.id: {
            "title": _document_title(row.title, row.filename),
        }
        for row in rows
    }


async def list_documents(
    db: AsyncSession, *, publication_id: str, organization_id: int
) -> list[dict[str, Any]]:
    """管理面：列出白名单行（含当前是否可检索，便于清理失效项）。"""
    publication = await get_publication(
        db, publication_id=publication_id, organization_id=organization_id
    )
    rows = (
        await db.execute(
            select(
                DocumentPublication.document_id,
                Document.title,
                Document.filename,
                Document.status,
                Document.is_enabled,
                Document.knowledge_base_id,
                Document.organization_id,
                Document.visibility,
            )
            .join(Document, Document.id == DocumentPublication.document_id)
            .where(
                DocumentPublication.publication_id == publication.id,
                DocumentPublication.organization_id == publication.organization_id,
            )
            .order_by(DocumentPublication.created_at)
        )
    ).all()
    documents = []
    for row in rows:
        searchable = (
            row.organization_id == publication.organization_id
            and row.knowledge_base_id == publication.knowledge_base_id
            and row.status == DocumentStatus.INDEXED
            and bool(row.is_enabled)
            and row.visibility == DocumentVisibility.ORGANIZATION
        )
        documents.append(
            {
                "id": row.document_id,
                "title": _document_title(row.title, row.filename),
                "status": _status_value(row.status),
                "searchable": searchable,
            }
        )
    return documents


async def list_candidates(
    db: AsyncSession, *, organization_id: int, knowledge_base_id: str
) -> dict[str, Any]:
    """管理面：发布选择器的候选文档（本组织 + 同 KB + INDEXED + 启用，有界）。"""
    await _get_knowledge_base(
        db, organization_id=organization_id, knowledge_base_id=knowledge_base_id
    )
    rows = (
        await db.execute(
            select(Document.id, Document.title, Document.filename)
            .where(
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == DocumentStatus.INDEXED,
                Document.is_enabled.is_(True),
                Document.visibility == DocumentVisibility.ORGANIZATION,
            )
            .order_by(Document.created_at.desc())
            .limit(MAX_CANDIDATES_SCAN + 1)
        )
    ).all()
    truncated = len(rows) > MAX_CANDIDATES_SCAN
    return {
        "documents": [
            {
                "id": row.id,
                "title": _document_title(row.title, row.filename),
            }
            for row in rows[:MAX_CANDIDATES_SCAN]
        ],
        "truncated": truncated,
    }


async def list_publications(
    db: AsyncSession, *, organization_id: int, knowledge_base_id: str | None = None
) -> list[dict[str, Any]]:
    """管理面：本组织发布列表（可按知识库过滤）；不含 token。"""
    conditions = [KbPublication.organization_id == organization_id]
    if knowledge_base_id:
        conditions.append(KbPublication.knowledge_base_id == knowledge_base_id)
    rows = (
        (
            await db.execute(
                select(KbPublication).where(*conditions).order_by(KbPublication.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    result = []
    for publication in rows:
        kb_name, count = await publication_meta(db, publication)
        result.append(
            publication_view(publication, knowledge_base_name=kb_name, document_count=count)
        )
    return result


async def resolve_public(db: AsyncSession, token: str) -> KbPublication:
    """公开 token 解析：无效统一 404，不区分禁用/撤销/过期/不存在。"""
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise NotFoundError(PUBLICATION_INVALID)
    publication = (
        await db.execute(
            select(KbPublication).where(KbPublication.token_hash == share_token_digest(token))
        )
    ).scalar_one_or_none()
    if publication is None or not publication_is_active(publication):
        raise NotFoundError(PUBLICATION_INVALID)
    return publication


def public_metadata(publication: KbPublication) -> dict[str, Any]:
    """公开元数据白名单：只有展示字段，无组织/知识库/内部 id。"""
    return {
        "title": publication.title,
        "description": publication.description,
        "expires_at": _iso(publication.expires_at),
    }


async def public_search(
    db: AsyncSession,
    *,
    publication: KbPublication,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """匿名只读检索：显式白名单 → ES 硬过滤 → 结果再按白名单二次过滤。

    返回字段严格为 ``rank/title/snippet/score``，不含 document_id/chunk_id/
    文件路径/组织/上传者。空白名单返回 ``[]``，绝不回退到 KB 全量。
    """
    if not isinstance(query, str):
        raise ValidationError("检索词必须是字符串", field="query")
    cleaned = query.strip()
    if not cleaned:
        raise ValidationError("检索词不能为空", field="query")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise ValidationError(
            f"检索词不能超过 {MAX_QUERY_CHARS} 字符", field="query", max=MAX_QUERY_CHARS
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not (1 <= top_k <= MAX_TOP_K):
        raise ValidationError(
            f"top_k 必须在 1 到 {MAX_TOP_K} 之间", field="top_k", min=1, max=MAX_TOP_K
        )

    whitelist = await _searchable_whitelist(db, publication)
    if not whitelist:
        return []

    pipeline = get_rag_pipeline()
    # viewer_user_id=None：pipeline 只在 viewer 非 None 时才读 ACL；白名单是
    # 本次检索的唯一授权来源。document_ids 是 ES 硬 terms 过滤，且命中结果
    # 返回后仍会按白名单再次过滤（下方 mutation point），两层都不能省。
    results, _debug = await pipeline.search_knowledge_base(
        cleaned,
        publication.organization_id,
        top_k=top_k,
        document_ids=sorted(whitelist),
        viewer_user_id=None,
        # 公开面固定 hybrid（不跑自动路由的 HyDE/查询分解）：匿名检索不应
        # 消耗不可预测的 LLM 预算；成本上限由 query/top_k + 限流共同约束。
        search_mode="hybrid",
    )

    public_results: list[dict[str, Any]] = []
    for hit in results or []:
        source = hit.get("_source") or {}
        document_id = str(hit.get("document_id") or source.get("doc_id") or "")
        # 变异点：删除本白名单复查 → 非白名单文档会进入公开响应（测试必红）。
        if document_id not in whitelist:
            continue
        raw_score = hit.get("score")
        if raw_score is None:
            raw_score = hit.get("rank_score", 0)
        try:
            score = float(raw_score or 0)
        except (TypeError, ValueError):
            score = 0.0
        snippet_source = (
            hit.get("snippet")
            or hit.get("original_text")
            or hit.get("content")
            or source.get("content")
            or ""
        )
        public_results.append(
            {
                "rank": len(public_results) + 1,
                "title": whitelist[document_id]["title"],
                "snippet": _sanitize_snippet(snippet_source),
                "score": round(score, 6),
            }
        )
        if len(public_results) >= top_k:
            break
    return public_results
