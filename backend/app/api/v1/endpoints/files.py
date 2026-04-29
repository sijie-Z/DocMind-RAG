"""
文件上传API端点 - 已修复头像上传500错误
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import Request

from app.core.database import get_db
from app.core.security import get_current_user, permission_required
from app.models.user import User
from app.models.notification import Notification
from app.services.file_service import file_service
from app.services.audit_service import audit_service
from app.schemas.file import (
    FileUploadResponse, 
    FileChunkUploadResponse,
    FileListResponse,
    FileDeleteResponse
)
from app.models.rbac import PermissionType

router = APIRouter()

@router.post("/upload", response_model=FileUploadResponse, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_UPLOAD]))])
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        current_user = await db.merge(current_user)
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
        organization_id = str(current_user.organization_id) if current_user.organization_id else "1"

        result = await file_service.upload_file(
            file=file, organization_id=organization_id,
            user_id=current_user.id, description=description, tags=tag_list
        )
        
        notification = Notification(
            user_id=current_user.id,
            title="文件上传成功",
            content=f"文件 \"{file.filename}\" 已成功上传。",
            type="file"
        )
        db.add(notification)
        await db.commit()
        return FileUploadResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
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
        setattr(current_user, "avatar_url", avatar_url)
        
        # 4. 创建成功通知
        notification = Notification(
            user_id=current_user.id,
            title="头像更新成功",
            content="您的个人头像已成功更新。",
            type="account"
        )
        db.add(notification)
        
        await db.commit()
        await db.refresh(current_user)
        
        return {"url": avatar_url, "success": True}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"头像上传失败: {str(e)}")

@router.post("/upload-chunk", response_model=FileChunkUploadResponse)
async def upload_file_chunk(
    chunk_data: bytes = File(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file_name: str = Form(...),
    file_hash: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        organization_id = str(current_user.organization_id) if current_user.organization_id else "1"
        result = await file_service.upload_chunk(
            chunk_data=chunk_data, chunk_index=chunk_index, total_chunks=total_chunks,
            file_name=file_name, file_hash=file_hash, organization_id=organization_id, user_id=current_user.id
        )
        return FileChunkUploadResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list", response_model=FileListResponse)
async def get_document_list(
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        organization_id = str(current_user.organization_id) if current_user.organization_id else "1"
        result = await file_service.get_document_list(
            organization_id=organization_id, user_id=user_id, status=status, skip=skip, limit=limit
        )
        return FileListResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{document_id}", response_model=FileDeleteResponse, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_DELETE]))])
async def delete_document(
    request: Request,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        organization_id = str(current_user.organization_id) if current_user.organization_id else "1"
        success = await file_service.delete_document(
            document_id=document_id, organization_id=organization_id, user_id=current_user.id
        )
        if success:
            await audit_service.log_activity(
                user_id=current_user.id,
                action="delete_file",
                target_type="document",
                target_id=document_id,
                detail=f"用户删除了文档 {document_id}",
                request=request
            )
        return FileDeleteResponse(success=success, message="删除成功" if success else "删除失败")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
         
         