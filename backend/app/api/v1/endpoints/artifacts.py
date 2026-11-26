"""Agent 工件 API（竞品批次七）：创建 / 列表 / 详情 / 版本史 / 正文下载。

口径：
- 读取按组织 + 用户双重隔离；跨组织与不存在一律 404（不回显存在性）。
  平台超管同文档读取先例：``is_superuser`` 不受组织/用户限定。
- 创建端点供 Agent 工具与内部调用方使用（``chat:write`` scope + 文档读权限）；
  用户创建工件是显式写操作，不是旁路。
- 正文端点只放行 ``status == ready`` 的工件，流式返回、MIME 取自入库值；
  同时返回 ``Content-Disposition`` 供下载（文件名经服务端清洗，不含路径）。
- 鉴权风格与既有资源端点一致：``permission_required`` + ``require_scope`` +
  端点内 ``require_organization``（fail-closed：无组织 403）。
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    get_current_user,
    permission_required,
    require_organization,
    require_scope,
)
from app.exceptions import (
    AppError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.models.rbac import PermissionType
from app.models.user import User
from app.services import artifact_service, artifact_share_service

logger = logging.getLogger(__name__)
router = APIRouter()

_READ_DEPS = [
    Depends(require_scope("chat:read")),
    Depends(permission_required([PermissionType.DOCUMENT_READ])),
]
_WRITE_DEPS = [
    Depends(require_scope("chat:write")),
    Depends(permission_required([PermissionType.DOCUMENT_READ])),
]


class ArtifactCreate(BaseModel):
    """创建工件：kind/title/content 必填；其余为可选溯源信息。"""

    kind: str = Field(..., max_length=32, description="text/markdown/json/diff/code/html/svg")
    title: str = Field(..., max_length=200, description="展示标题（同时决定默认文件名）")
    content: str = Field(..., description="工件正文（UTF-8，≤512 KiB）")
    filename: str | None = Field(None, max_length=255)
    conversation_id: str | None = Field(None, max_length=36)
    source_tool: str | None = Field(None, max_length=128)


class ArtifactShareCreate(BaseModel):
    """创建/轮换分享：可选过期天数（1-90；缺省不过期）。"""

    expires_in_days: int | None = Field(
        None, description=f"过期天数（1-{artifact_share_service.MAX_EXPIRY_DAYS}）；缺省不过期"
    )


@router.post("", response_model=dict, dependencies=_WRITE_DEPS)
async def create_artifact(
    body: ArtifactCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建工件（同作用域同标题同内容幂等返回既有行；新内容追加版本）。

    Agent 工具内部走 ``artifact_service.create_artifact``（同一服务入口），
    本端点面向显式客户端调用；两者校验/版本语义完全一致。
    """
    try:
        org_id = await require_organization(current_user, db)
        row, created = await artifact_service.create_artifact(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            kind=body.kind,
            title=body.title,
            content=body.content,
            conversation_id=body.conversation_id,
            filename=body.filename,
            source_tool=body.source_tool,
        )
        await db.commit()
        await db.refresh(row)
        data = artifact_service.artifact_summary(row)
        data["created"] = created
        return {"success": True, "data": data}
    except (ValidationError, NotFoundError, AuthorizationError):
        await db.rollback()
        raise
    except AppError:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Create artifact failed: {type(e).__name__}: {e}")
        raise AppError("创建工件失败") from e


@router.get("", response_model=dict, dependencies=_READ_DEPS)
async def list_artifacts(
    conversation_id: str | None = Query(None, max_length=36, description="按会话过滤"),
    task_id: str | None = Query(None, max_length=36, description="按 Agent 任务过滤"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户在本组织内的工件（最新在前；可按会话/任务过滤）。"""
    try:
        org_id = await require_organization(current_user, db)
        rows = await artifact_service.list_artifacts(
            db,
            organization_id=org_id,
            user_id=None if current_user.is_superuser else current_user.id,
            conversation_id=conversation_id,
            task_id=task_id,
            limit=limit,
        )
        return {
            "success": True,
            "data": {
                "items": [artifact_service.artifact_summary(row) for row in rows],
                "total": len(rows),
            },
        }
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List artifacts failed: {type(e).__name__}: {e}")
        raise AppError("获取工件列表失败") from e


@router.get("/{artifact_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """工件详情 + 完整版本链（旧 → 新）。"""
    try:
        org_id = await require_organization(current_user, db)
        row = await artifact_service.get_artifact(
            db,
            artifact_id=artifact_id,
            organization_id=org_id,
            user_id=None if current_user.is_superuser else current_user.id,
        )
        chain = await artifact_service.version_history(
            db,
            artifact=row,
            organization_id=org_id,
            user_id=None if current_user.is_superuser else current_user.id,
        )
        data = artifact_service.artifact_summary(row)
        data["versions"] = [artifact_service.artifact_to_dict(item) for item in chain]
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get artifact failed: {type(e).__name__}: {e}")
        raise AppError("获取工件详情失败") from e


@router.get("/{artifact_id}/content", dependencies=_READ_DEPS)
async def get_artifact_content(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """工件正文（流式；MIME 取自入库值，仅 ready 工件可读）。

    安全：对象名由服务端从行内读取，客户端只能给工件 id（路径不可注入）；
    文件名经服务端清洗后进 ``Content-Disposition``。HTML/SVG 按原 MIME 返回，
    渲染方必须自行沙箱化（前端在 sandbox iframe 中展示）。
    """
    try:
        org_id = await require_organization(current_user, db)
        row = await artifact_service.get_artifact(
            db,
            artifact_id=artifact_id,
            organization_id=org_id,
            user_id=None if current_user.is_superuser else current_user.id,
        )
        payload = await artifact_service.read_content(row)
        filename = (row.filename or f"artifact-{row.id}").replace('"', "_")
        # HTTP 头是 latin-1：非 ASCII 文件名走 RFC 5987 的 filename*（UTF-8''…），
        # filename= 只保留 ASCII 兜底（不支持 filename* 的旧客户端至少拿到合法值）。
        ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
        quoted = quote(filename, safe="")
        return Response(
            content=payload,
            media_type=row.mime_type,
            headers={
                "Content-Disposition": (
                    f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=0, must-revalidate",
                # 工件可能是模型生成的 HTML/SVG：即使客户端直接打开该 URL，
                # 也必须运行在无同源权限的沙箱里（应用 UI 走 iframe srcdoc +
                # sandbox，另有防护）。没有这条，直开链接会在应用源上执行脚本。
                "Content-Security-Policy": (
                    "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
                ),
                "X-Frame-Options": "DENY",
            },
        )
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get artifact content failed: {type(e).__name__}: {e}")
        raise AppError("读取工件内容失败") from e


# ── 只读分享（批次九）──────────────────────────────────────────────────────
# 管理面严格 owner-only：不是「组织内任意成员」，超管也不绕过（服务层按
# organization_id + user_id 双限定，跨组织/他人 404）。
# 开关只门控「创建」与公开读取：撤销/状态查询必须始终可用（撤链接是安全操作）。


@router.post("/{artifact_id}/share", response_model=dict, dependencies=_WRITE_DEPS)
async def create_artifact_share(
    artifact_id: str,
    body: ArtifactShareCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建或轮换分享链接；token 明文仅本次响应返回（旧链接立即失效）。"""
    if not settings.ARTIFACT_PUBLIC_SHARE_ENABLED:
        # 不泄露部署开关状态：与「工件不存在」同一 404。
        raise NotFoundError("工件不存在")
    try:
        org_id = await require_organization(current_user, db)
        share, token = await artifact_share_service.create_or_rotate_share(
            db,
            artifact_id=artifact_id,
            organization_id=org_id,
            user_id=current_user.id,
            expires_in_days=body.expires_in_days,
        )
        data = artifact_share_service.share_status(share)
        data["token"] = token
        data["url"] = f"/share/artifact/{token}"
        await db.commit()
        return {"success": True, "data": data}
    except (ValidationError, NotFoundError, AuthorizationError):
        await db.rollback()
        raise
    except AppError:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Create artifact share failed: {type(e).__name__}: {e}")
        raise AppError("创建分享链接失败") from e


@router.get("/{artifact_id}/share", response_model=dict, dependencies=_READ_DEPS)
async def get_artifact_share_status(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询分享状态（不返回 token；token 只在创建/轮换时给一次）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await artifact_share_service.get_share_status(
            db,
            artifact_id=artifact_id,
            organization_id=org_id,
            user_id=current_user.id,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get artifact share status failed: {type(e).__name__}: {e}")
        raise AppError("获取分享状态失败") from e


@router.delete("/{artifact_id}/share", response_model=dict, dependencies=_WRITE_DEPS)
async def revoke_artifact_share(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销分享：撤销后旧 token 立即失效（包括已打开的页面刷新时）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await artifact_share_service.revoke_share(
            db,
            artifact_id=artifact_id,
            organization_id=org_id,
            user_id=current_user.id,
        )
        await db.commit()
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        await db.rollback()
        raise
    except AppError:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Revoke artifact share failed: {type(e).__name__}: {e}")
        raise AppError("撤销分享失败") from e
