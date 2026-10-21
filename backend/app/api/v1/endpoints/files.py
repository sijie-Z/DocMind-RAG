"""
文件上传API端点 - 已修复头像上传500错误

兼容壳（#416）：/files 下的文档管理端点（upload / upload-chunk / list /
{id} 删除）保留可用，但已标记 deprecated，规范链路为：
- 上传：POST /documents/upload
- 删除：DELETE /knowledge/document/{id} —— 统一 knowledge 删除链路，
  显式清 document_chunks / document_tags / knowledge_processing_jobs、
  ES 索引与 RAG 缓存后再删文档行。
/files/upload/avatar 不在弃用范围：它是头像上传的唯一入口，前端仍在调用。
"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    get_document_for_user,
    permission_required,
    require_organization,
)
from app.exceptions import AppError
from app.models.notification import Notification
from app.models.rbac import PermissionType
from app.models.user import User
from app.schemas.file import (
    FileChunkUploadResponse,
    FileDeleteResponse,
    FileListResponse,
    FileUploadResponse,
)
from app.services.audit_service import audit_service
from app.services.file_service import file_service
from app.services.knowledge_service import knowledge_service

router = APIRouter()


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    dependencies=[Depends(permission_required([PermissionType.DOCUMENT_UPLOAD]))],
    deprecated=True,
)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    tags: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        current_user = await db.merge(current_user)
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
        # S3 安全加固：无组织用户直接 403，不再兜底到 "1"
        organization_id = str(await require_organization(current_user, db))

        result = await file_service.upload_file(
            file=file,
            organization_id=organization_id,
            user_id=current_user.id,
            description=description,
            tags=tag_list,
        )

        notification = Notification(
            user_id=current_user.id,
            title="文件上传成功",
            content=f'文件 "{file.filename}" 已成功上传。',
            type="file",
        )
        db.add(notification)
        await db.commit()
        return FileUploadResponse(**result)
    except (AppError, HTTPException):
        # 校验类 4xx（AppError/HTTPException 家族）原样放行，不得被下面的
        # except Exception 重包成 500（round2 后端卫生修复③；issue #98 同款）。
        raise
    except Exception as e:
        raise AppError(str(e))


# 头像上传不在 /files 文档管理弃用范围内：它是当前唯一的头像上传入口，
# 前端 src/api/user.ts 仍在调用。
@router.post("/upload/avatar", response_model=dict)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    上传用户头像 - 修复了重复定义和Session绑定问题
    """
    try:
        # 1. 确保用户对象绑定到当前异步 Session
        current_user = await db.merge(current_user)

        # 2. 调用 service 处理文件上传
        avatar_url = await file_service.upload_avatar(file, current_user.id)

        # 3. 更新数据库字段（避免类型检查将 ORM 列描述符误判为 Column[str]）
        current_user.avatar_url = avatar_url

        # 4. 创建成功通知
        notification = Notification(
            user_id=current_user.id,
            title="头像更新成功",
            content="您的个人头像已成功更新。",
            type="account",
        )
        db.add(notification)

        await db.commit()
        await db.refresh(current_user)

        return {"url": avatar_url, "success": True}

    except (AppError, HTTPException):
        # 校验类 4xx（类型/大小/MIME 不合规）原样放行——此前被下面的
        # except Exception 重包成裸 AppError(500)（round2 后端卫生修复③，
        # K 支线评审 medium；_media_audit §3.4 实测 500 的最后一段包装）。
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise AppError(f"头像上传失败: {str(e)}")


@router.post("/upload-chunk", response_model=FileChunkUploadResponse, deprecated=True)
async def upload_file_chunk(
    chunk_data: bytes = File(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file_name: str = Form(...),
    file_hash: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # S3 安全加固：无组织用户直接 403，不再兜底到 "1"
        organization_id = str(await require_organization(current_user, db))
        result = await file_service.upload_chunk(
            chunk_data=chunk_data,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            file_name=file_name,
            file_hash=file_hash,
            organization_id=organization_id,
            user_id=current_user.id,
        )
        return FileChunkUploadResponse(**result)
    except (AppError, HTTPException):
        # 同 /upload：分块合并阶段的校验 4xx 原样放行（round2 卫生修复③）
        raise
    except Exception as e:
        raise AppError(str(e))


@router.get("/list", response_model=FileListResponse, deprecated=True)
async def get_document_list(
    user_id: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # S3 安全加固：无组织用户直接 403，不再兜底到 "1"
        organization_id = str(await require_organization(current_user, db))
        result = await file_service.get_document_list(
            organization_id=organization_id, user_id=user_id, status=status, skip=skip, limit=limit
        )
        return FileListResponse(**result)
    except AppError:
        raise
    except Exception as e:
        raise AppError(str(e))


@router.delete(
    "/{document_id}",
    response_model=FileDeleteResponse,
    dependencies=[Depends(permission_required([PermissionType.DOCUMENT_DELETE]))],
    deprecated=True,
)
async def delete_document(
    request: Request,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    删除文档（兼容端点；规范入口为 DELETE /api/v1/knowledge/document/{id}）。

    归属 + org 校验由 get_document_for_user 统一完成（文档不存在 404、
    跨组织 403，与知识删除端点同语义）；删除委托 knowledge_service 的统一
    链路 —— 显式清 document_chunks / document_tags /
    knowledge_processing_jobs、ES 索引与 RAG 缓存后再删文档行。

    此前这里走 file_service.delete_document：只删 documents 主表，既会被
    MySQL 的 NO ACTION 外键挡住（1451 → 500），也不会清理 ES/缓存
    （#416）。
    """
    try:
        doc = await get_document_for_user(db, current_user, document_id)
        # knowledge_service.delete_knowledge 内部即 delete_knowledge_thoroughly
        # —— 与 DELETE /api/v1/knowledge/document/{id} 完全同一条链路。
        # 返回 False 的分支当前不可达（service 只会返回 True 或抛 AppError），
        # 但保留对返回值的消费，避免将来出现 False 时响应谎报成功。
        success = await knowledge_service.delete_knowledge(document_id, doc.organization_id)

        if success:
            await audit_service.log_activity(
                user_id=current_user.id,
                action="delete_file",
                target_type="document",
                target_id=document_id,
                detail=f"用户删除了文档 {document_id}",
                request=request,
            )
        return FileDeleteResponse(success=success, message="删除成功" if success else "删除失败")
    except AppError:
        raise
    except Exception as e:
        raise AppError(str(e))
