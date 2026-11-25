"""Agent 工件的创建/读取/版本链（竞品批次七）。

边界：
- **单表内容寻址**：一行 = 一份不可变内容（``content_sha256`` + 确定性 MinIO
  对象名 ``artifacts/{org}/{sha256[:2]}/{sha256}{ext}``）。同一内容在同一链上
  重复创建返回**同一行**（幂等，不刷版本号）；内容变化才追加版本行。
- **版本链**：``replaces_artifact_id`` 自引用。链的锚点是「同
  ``(organization_id, user_id, conversation_id|task_id, title)`` 作用域内的根行」，
  新内容以当前链头为父追加 ``version_no = parent.version_no + 1``。链上每一行
  都完整可读/可下载（历史版本不被改写）。
- **租户/用户隔离**：所有读取按 ``organization_id`` + ``user_id`` 双重限定；
  跨组织与不存在一律 ``NotFoundError``（不回显存在性）。平台超管
  （``user_id=None``）按平台口径可读全部（与文档读取先例一致）。
- **对象写入顺序**：先写 MinIO（确定性对象名，重复写幂等）→ 再插行；对象写
  失败如实抛 ``ServiceUnavailableError``，绝不留下「行指向缺失内容」的窗口。
- **有界**：正文上限 ``MAX_CONTENT_BYTES``（UTF-8 字节），标题/文件名/工具名
  亦有长度上限；超限抛 ``ValidationError``（不用静默截断——截断的工件是坏工件）。

不提供删除路径：工件是审计/复用资产（与核验模板版本同口径）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.minio_client import minio_client
from app.exceptions import NotFoundError, ServiceUnavailableError, ValidationError
from app.models.artifact import ARTIFACT_KINDS, AgentArtifact

# ── 上限（schema 层同值；服务层重复校验，防内部调用绕过 schema）──────────────
MAX_CONTENT_BYTES = 512 * 1024  # 工具体积上限：超过的是文件上传，不是工件
MAX_TITLE_CHARS = 200
MAX_FILENAME_CHARS = 255
MAX_SOURCE_TOOL_CHARS = 128
MAX_SOURCE_CALL_ID_CHARS = 64
MAX_AGENT_ID_CHARS = 64
MAX_LIST_LIMIT = 100
MAX_CHAIN_DEPTH = 1000  # 版本链回溯深度上限（合法数据线性递增，环/损坏兜底）

# kind → (MIME, 默认扩展名)。预览端按 MIME 决定渲染方式；html/svg 只在
# sandbox iframe 中渲染（前端职责，后端只如实标注类型）。
KIND_MEDIA: dict[str, tuple[str, str]] = {
    "text": ("text/plain; charset=utf-8", "txt"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "json": ("application/json; charset=utf-8", "json"),
    "diff": ("text/x-diff; charset=utf-8", "patch"),
    "code": ("text/plain; charset=utf-8", "txt"),
    "html": ("text/html; charset=utf-8", "html"),
    "svg": ("image/svg+xml; charset=utf-8", "svg"),
}

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _sanitize_text(value: str | None, *, max_chars: int, field: str) -> str:
    """单行化 + 去控制字符（展示字段）；超长抛 ValidationError。"""
    text = (value or "").strip()
    text = "".join(ch for ch in text if ch.isprintable())
    text = " ".join(text.split())
    if len(text) > max_chars:
        raise ValidationError(
            f"字段长度超过上限（{max_chars} 字符）",
            field=field,
            max_chars=max_chars,
            found=len(text),
        )
    return text


def normalize_kind(kind: str | None) -> str:
    """kind 归一化：未知类型一律拒绝（不猜、不降级成 text）。"""
    normalized = (kind or "").strip().lower()
    if normalized not in ARTIFACT_KINDS:
        raise ValidationError(
            "不支持的工件类型",
            field="kind",
            expected=list(ARTIFACT_KINDS),
            found=kind,
        )
    return normalized


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def object_name_for(organization_id: int, sha256: str, kind: str) -> str:
    """确定性对象名：``artifacts/{org}/{sha[:2]}/{sha}.{ext}``。

    组织前缀提供租户级隔离的可读性；内容哈希保证重复写幂等（同内容 → 同对象，
    MinIO put 覆盖为同一字节，无孤儿）。所有段都经过白名单清洗，不可能逃逸前缀。
    """
    if not _HEX_64_RE.match(sha256):  # pragma: no cover — 内部调用恒为 sha256
        raise ValueError("sha256 必须是 64 位小写十六进制")
    ext = KIND_MEDIA.get(kind, KIND_MEDIA["text"])[1]
    org_segment = _SAFE_SEGMENT_RE.sub("_", str(int(organization_id)))
    return f"artifacts/{org_segment}/{sha256[:2]}/{sha256}.{ext}"


def default_filename(title: str, kind: str) -> str:
    """由标题派生文件名（``{slug}.{ext}``；slug 空时回退 'artifact'）。

    slug 允许 Unicode 字母/数字（中文标题得到中文文件名，下载体验才不突兀）；
    路径分隔符与控制字符被替换为 ``_``——文件名永远不会带出目录语义。
    HTTP 头的 latin-1 编码问题由端点用 RFC 5987 的 ``filename*`` 处理。
    """
    slug = re.sub(r"[^\w.-]+", "_", (title or "").strip(), flags=re.UNICODE).strip("._-")[:80]
    ext = KIND_MEDIA.get(kind, KIND_MEDIA["text"])[1]
    return f"{slug or 'artifact'}.{ext}"


def artifact_to_dict(row: AgentArtifact) -> dict[str, Any]:
    """对外投影（列表/详情同一口径）。不包含对象名等存储内部字段。"""
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "task_id": row.task_id,
        "agent_id": row.agent_id,
        "kind": row.kind,
        "title": row.title,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "content_sha256": row.content_sha256,
        "file_size": row.file_size,
        "source_tool": row.source_tool,
        "source_call_id": row.source_call_id,
        "version_no": row.version_no,
        "replaces_artifact_id": row.replaces_artifact_id,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def artifact_summary(row: AgentArtifact) -> dict[str, Any]:
    """SSE / 列表摘要：投影 + 内容地址（前端右栏凭它拉正文）。"""
    data = artifact_to_dict(row)
    data["content_url"] = f"/api/v1/artifacts/{row.id}/content"
    return data


async def _upload_content(sha256: str, kind: str, content: str, organization_id: int) -> str:
    """写 MinIO（确定性对象名）；失败如实 503，不吞成「成功但没内容」。"""
    object_name = object_name_for(organization_id, sha256, kind)
    payload = content.encode("utf-8")
    mime = KIND_MEDIA.get(kind, KIND_MEDIA["text"])[0]
    try:
        await asyncio.to_thread(
            minio_client.put_object,
            object_name=object_name,
            data=BytesIO(payload),
            length=len(payload),
            content_type=mime,
        )
    except Exception as exc:  # noqa: BLE001 — 存储故障必须对外可判定
        raise ServiceUnavailableError(
            "工件内容存储失败，请稍后重试",
            error_code="ARTIFACT_STORAGE_UNAVAILABLE",
        ) from exc
    return object_name


def _scope_conditions(
    *,
    organization_id: int,
    user_id: int | None,
    conversation_id: str | None,
    task_id: str | None,
) -> list[Any]:
    conditions: list[Any] = [AgentArtifact.organization_id == organization_id]
    if user_id is not None:
        conditions.append(AgentArtifact.user_id == user_id)
    if conversation_id is not None:
        conditions.append(AgentArtifact.conversation_id == conversation_id)
    if task_id is not None:
        conditions.append(AgentArtifact.task_id == task_id)
    return conditions


async def _find_chain_head(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    title: str,
    conversation_id: str | None,
    task_id: str | None,
) -> AgentArtifact | None:
    """在相同作用域 + 标题下找无后继的最新行（版本链头）。

    作用域用 ``conversation_id`` / ``task_id`` 精确匹配（含 NULL 语义：两者都空
    的工件只在"无会话无任务"的同名链内演进）。标题精确匹配——不同标题是不同
    工件，不做模糊归并（误归并会把两份无关产出串成一条假的版本史）。
    """
    child = aliased(AgentArtifact)
    has_child = exists().where(child.replaces_artifact_id == AgentArtifact.id)
    stmt = (
        select(AgentArtifact)
        .where(
            *_scope_conditions(
                organization_id=organization_id,
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
            ),
            AgentArtifact.title == title,
            ~has_child,
        )
        .order_by(AgentArtifact.version_no.desc(), AgentArtifact.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def _row_for_call(
    session: AsyncSession, *, task_id: str, source_call_id: str
) -> AgentArtifact | None:
    return (
        (
            await session.execute(
                select(AgentArtifact)
                .where(
                    AgentArtifact.task_id == task_id,
                    AgentArtifact.source_call_id == source_call_id,
                )
                .order_by(AgentArtifact.version_no.desc())
            )
        )
        .scalars()
        .first()
    )


async def create_artifact(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    kind: str,
    title: str,
    content: str,
    conversation_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    filename: str | None = None,
    source_tool: str | None = None,
    source_call_id: str | None = None,
) -> tuple[AgentArtifact, bool]:
    """创建（或复用）一个工件版本。

    返回 ``(行, created)``：``created=False`` 表示已存在等价行（幂等），调用方
    应直接使用既有工件 id，不重复发 SSE。

    幂等/去重语义：
    1. 同 ``(task_id, source_call_id)`` 已有行 → 直接返回（resume 重放防重复）；
    2. 同链头内容哈希一致 → 直接返回链头（重复产出相同内容不刷版本号）；
    3. 否则追加新版本行（``version_no = 链头版本 + 1``）。
    """
    normalized_kind = normalize_kind(kind)
    safe_title = _sanitize_text(title, max_chars=MAX_TITLE_CHARS, field="title")
    if not safe_title:
        raise ValidationError("工件标题不能为空", field="title")
    if not isinstance(content, str) or not content:
        raise ValidationError("工件内容不能为空", field="content")
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise ValidationError(
            f"工件内容超过上限（{MAX_CONTENT_BYTES} 字节）",
            field="content",
            max_bytes=MAX_CONTENT_BYTES,
            found=size,
        )
    safe_filename = (
        _sanitize_text(filename, max_chars=MAX_FILENAME_CHARS, field="filename") or None
        if filename
        else None
    )
    if safe_filename is None:
        safe_filename = default_filename(safe_title, normalized_kind)
    safe_tool = (
        _sanitize_text(source_tool, max_chars=MAX_SOURCE_TOOL_CHARS, field="source_tool") or None
        if source_tool
        else None
    )
    safe_call = (
        _sanitize_text(source_call_id, max_chars=MAX_SOURCE_CALL_ID_CHARS, field="source_call_id")
        or None
        if source_call_id
        else None
    )
    safe_agent = (
        _sanitize_text(agent_id, max_chars=MAX_AGENT_ID_CHARS, field="agent_id") or None
        if agent_id
        else None
    )

    sha256 = content_sha256(content)
    mime = KIND_MEDIA.get(normalized_kind, KIND_MEDIA["text"])[0]

    # ① 调用级幂等：同一工具调用至多一行（与 DB 唯一约束同语义，先查后插）。
    if task_id and safe_call:
        existing = await _row_for_call(session, task_id=task_id, source_call_id=safe_call)
        if existing is not None:
            return existing, False

    # ② 内容级幂等：链头内容相同 → 不制造空版本。
    # 版本链的身份是「同一会话里的同一标题」，不是单次运行：task_id 是每次
    # agent 运行的记账 id，若参与链头匹配，跨 run 复用标题会永远回到 v1
    # （工具描述承诺的是同一会话内追加版本）。只有在没有会话时才退化到任务作用域。
    parent = await _find_chain_head(
        session,
        organization_id=organization_id,
        user_id=user_id,
        title=safe_title,
        conversation_id=conversation_id,
        task_id=None if conversation_id is not None else task_id,
    )
    if parent is not None and parent.content_sha256 == sha256:
        return parent, False

    version_no = 1 if parent is None else int(parent.version_no) + 1

    # ③ 先写对象再插行：对象名确定性且内容寻址，重复写是幂等覆盖；
    #    写失败 → 503，DB 不留下指向缺失内容的行。
    object_name = await _upload_content(sha256, normalized_kind, content, organization_id)

    row = AgentArtifact(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        conversation_id=conversation_id,
        task_id=task_id,
        agent_id=safe_agent,
        kind=normalized_kind,
        title=safe_title,
        filename=safe_filename,
        mime_type=mime,
        content_sha256=sha256,
        file_size=size,
        storage_object_name=object_name,
        source_tool=safe_tool,
        source_call_id=safe_call,
        version_no=version_no,
        replaces_artifact_id=parent.id if parent is not None else None,
        status="ready",
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # 并发同调用双写：对方已插行 → 回滚本行并返回既有行（幂等）。
        await session.rollback()
        if task_id and safe_call:
            existing = await _row_for_call(session, task_id=task_id, source_call_id=safe_call)
            if existing is not None:
                return existing, False
        raise
    return row, True


async def list_artifacts(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int | None,
    conversation_id: str | None = None,
    task_id: str | None = None,
    limit: int = MAX_LIST_LIMIT,
) -> list[AgentArtifact]:
    """按作用域列工件（最新在前）。``user_id=None`` 仅限超管平台口径。"""
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    stmt = (
        select(AgentArtifact)
        .where(
            *_scope_conditions(
                organization_id=organization_id,
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
            )
        )
        .order_by(AgentArtifact.created_at.desc(), AgentArtifact.version_no.desc())
        .limit(bounded)
    )
    return list((await session.execute(stmt)).scalars().all())


async def artifacts_for_call(
    session: AsyncSession, *, task_id: str, source_call_id: str
) -> AgentArtifact | None:
    """工具调用维度的工件查找（SSE 关联/回放用）。"""
    return await _row_for_call(session, task_id=task_id, source_call_id=source_call_id)


async def get_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    organization_id: int,
    user_id: int | None,
) -> AgentArtifact:
    """按 id 读取（组织 + 用户双重限定）；跨组织/不存在一律 404。"""
    row = (
        (
            await session.execute(
                select(AgentArtifact).where(
                    AgentArtifact.id == artifact_id,
                    *_scope_conditions(
                        organization_id=organization_id,
                        user_id=user_id,
                        conversation_id=None,
                        task_id=None,
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None or row.status != "ready":
        raise NotFoundError("工件不存在", resource="artifact", resource_id=artifact_id)
    return row


async def version_history(
    session: AsyncSession,
    *,
    artifact: AgentArtifact,
    organization_id: int,
    user_id: int | None,
) -> list[AgentArtifact]:
    """返回该工件所在的完整版本链（旧 → 新）。

    链是线性的（每次追加只有一个父），但读取可能从任意一行开始；因此先在同
    作用域内建 id → 行 映射，再从当前行回溯到根、自根沿子指针前进，得到完整
    有序链（含当前行之后的新版本）。作用域内的行量很小（同会话同标题），
    单查询即可，不做递归 SQL。
    """
    scope = _scope_conditions(
        organization_id=organization_id,
        user_id=user_id,
        conversation_id=artifact.conversation_id,
        task_id=artifact.task_id,
    )
    rows = list(
        (
            await session.execute(
                select(AgentArtifact)
                .where(*scope, AgentArtifact.title == artifact.title)
                .order_by(AgentArtifact.version_no.asc(), AgentArtifact.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    if artifact.id not in by_id:
        return [artifact]

    # 回溯到根。
    root = by_id[artifact.id]
    for _ in range(MAX_CHAIN_DEPTH):
        parent_id = root.replaces_artifact_id
        if not parent_id or parent_id not in by_id:
            break
        root = by_id[parent_id]

    # 自根沿子指针前进（每个父至多一个子；异常分叉时取版本号最小的子保持一致）。
    children: dict[str, list[AgentArtifact]] = {}
    for row in rows:
        if row.replaces_artifact_id:
            children.setdefault(row.replaces_artifact_id, []).append(row)
    chain: list[AgentArtifact] = [root]
    seen = {root.id}
    cursor = root
    for _ in range(MAX_CHAIN_DEPTH):
        next_rows = sorted(
            (row for row in children.get(cursor.id, []) if row.id not in seen),
            key=lambda row: (row.version_no, row.created_at or _now()),
        )
        if not next_rows:
            break
        cursor = next_rows[0]
        seen.add(cursor.id)
        chain.append(cursor)
    return chain


async def read_content(artifact: AgentArtifact) -> bytes:
    """从 MinIO 读正文（对象名由服务端掌握，客户端不可传路径）。"""
    if not artifact.storage_object_name:
        raise ServiceUnavailableError("工件内容不可用", error_code="ARTIFACT_CONTENT_MISSING")
    try:
        response = await asyncio.to_thread(minio_client.get_object, artifact.storage_object_name)
        try:
            return await asyncio.to_thread(response.read)
        finally:
            close = getattr(response, "close", None)
            release = getattr(response, "release_conn", None)
            if close is not None:
                await asyncio.to_thread(close)
            if release is not None:
                await asyncio.to_thread(release)
    except Exception as exc:  # noqa: BLE001 — 存储故障必须对外可判定（503）
        raise ServiceUnavailableError(
            "工件内容读取失败，请稍后重试",
            error_code="ARTIFACT_STORAGE_UNAVAILABLE",
        ) from exc


def parse_artifact_payload(result: Any) -> dict[str, Any] | None:
    """从工具结果里识别**显式工件载荷**（``{"kind": "artifact/v1", ...}``）。

    只认显式载荷：普通长文本绝不因"长得像文件"被固化成工件（误判比不升级更
    伤——与前端 artifact.ts 的设计原则一致）。两条结构化通道：

    - 工具返回 ``ToolResult``：读取 ``meta.extra["artifact"]``（推荐通道，
      载荷不经字符串序列化，正文保真）；
    - 工具返回字符串 / 结构化 data：解析 JSON 后检查顶层 ``kind`` 是否为
      ``artifact/v1``（工具作者显式声明）。

    载荷字段：``title``（必填）、``content``（必填正文），可选 ``kind``
    （默认 text；标记值 ``artifact/v1`` 占用 ``kind`` 时读取 ``artifact_kind``）、
    ``filename``。载荷不合法时返回 None（按普通结果处理，绝不因载荷坏掉而打断
    run）。两条通道统一走 ``_normalize_payload``，语义不因序列化方式而分叉。
    """
    if result is None:
        return None
    meta = getattr(result, "meta", None)
    if meta is not None:
        extra = getattr(meta, "extra", None) or {}
        payload = extra.get("artifact")
        if isinstance(payload, dict):
            normalized = _normalize_payload(payload)
            if normalized is not None:
                return normalized
    # 结构化 data（dict）/ JSON 字符串通道：顶层 ``kind`` 必须是标记值
    # ``artifact/v1``，真实工件类型在 ``artifact_kind``（两键共存不混淆）。
    # 热路径守卫：工具结果动辄数百 KiB 且以普通文本内容开头——先做开销为 O(1)
    # 的标记子串预检，未命中就不做 JSON 解析（绝不在每次 tool_result 上白解析）。
    data = getattr(result, "data", None)
    candidates: list[Any] = []
    if isinstance(data, dict):
        candidates.append(data)
    text = result if isinstance(result, str) else str(result)
    stripped = text.lstrip()
    if stripped.startswith("{") and '"artifact/v1"' in stripped:
        try:
            candidates.append(json.loads(stripped))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for parsed in candidates:
        if not isinstance(parsed, dict) or parsed.get("kind") != "artifact/v1":
            continue
        return _normalize_payload(parsed)
    return None


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """把显式载荷归一成 ``{kind: <真实类型>, title, content, filename}``。"""
    if not isinstance(payload.get("content"), str):
        return None
    normalized = dict(payload)
    kind = payload.get("artifact_kind")
    if not kind:
        raw_kind = payload.get("kind")
        kind = raw_kind if raw_kind and raw_kind != "artifact/v1" else "text"
    normalized["kind"] = kind
    return normalized
