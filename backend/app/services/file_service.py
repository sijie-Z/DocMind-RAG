"""
文件服务 - 处理文件上传、存储和管理
"""
import os
import uuid
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List
import asyncio
import logging
from datetime import datetime

from fastapi import UploadFile, HTTPException
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentStatus, DocumentType
from app.services.document_parser import DocumentParser
from app.core.minio import get_minio_client

logger = logging.getLogger(__name__)


class FileUploadService:
    """文件上传服务 - 支持大文件分块上传"""
    
    def __init__(self):
        self.chunk_size = 5 * 1024 * 1024  # 5MB 分块大小
        self.allowed_extensions = {
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', 
            '.ppt', '.pptx', '.txt', '.md', '.csv'
        }
        self.allowed_avatar_extensions = {
            '.jpg', '.jpeg', '.png', '.gif', '.webp'
        }
        self.max_file_size = 100 * 1024 * 1024  # 100MB 最大文件大小
        self.max_avatar_size = 5 * 1024 * 1024 # 5MB
        self.document_service = DocumentParser()
    
    def _validate_file(self, filename: str, file_size: int, is_avatar: bool = False) -> None:
        """
        验证文件
        
        Args:
            filename: 文件名
            file_size: 文件大小
            is_avatar: 是否为头像
            
        Raises:
            HTTPException: 文件验证失败
        """
        # 检查文件扩展名
        file_extension = Path(filename).suffix.lower()
        allowed = self.allowed_avatar_extensions if is_avatar else self.allowed_extensions
        max_size = self.max_avatar_size if is_avatar else self.max_file_size
        
        if file_extension not in allowed:
            raise HTTPException(
                status_code=400, 
                detail=f"不支持的文件格式。支持的格式: {', '.join(allowed)}"
            )
        
        # 检查文件大小
        if file_size > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大。最大支持 {max_size / (1024*1024):.0f}MB"
            )
    
    def _generate_file_hash(self, content: bytes) -> str:
        """
        生成文件内容的哈希值
        
        Args:
            content: 文件内容
            
        Returns:
            文件哈希值
        """
        return hashlib.sha256(content).hexdigest()
    
    async def _upload_to_minio(self, file_content: bytes, object_name: str, content_type: str) -> str:
        """
        上传文件到MinIO
        
        Args:
            file_content: 文件内容
            object_name: 对象名称
            content_type: 内容类型
            
        Returns:
            文件访问URL
        """
        try:
            minio_client = get_minio_client()
            
            # 上传文件
            from io import BytesIO
            minio_client.put_object(
                settings.MINIO_BUCKET_NAME,
                object_name,
                BytesIO(file_content),
                length=len(file_content),
                content_type=content_type
            )
            
            # 生成访问URL
            url = f"{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET_NAME}/{object_name}"
            if not url.startswith('http'):
                url = f"http://{url}"
            return url
            
        except Exception as e:
            logger.error(f"上传文件到MinIO失败: {str(e)}")
            raise HTTPException(status_code=500, detail="文件存储失败")

    async def delete_file(self, file_path: str) -> bool:
        """
        从存储中删除文件 (支持 MinIO 和 本地文件)
        
        Args:
            file_path: 文件路径或对象名称
            
        Returns:
            是否删除成功
        """
        try:
            # 1. 尝试从 MinIO 删除
            try:
                minio_client = get_minio_client()
                # 如果是完整的 URL，提取对象名
                object_name = file_path
                if "://" in file_path:
                    # 假设 URL 格式为 http://endpoint/bucket/object_name
                    parts = file_path.split("/")
                    object_name = "/".join(parts[4:]) if len(parts) > 4 else parts[-1]
                
                minio_client.remove_object(settings.MINIO_BUCKET_NAME, object_name)
                logger.info(f"已从 MinIO 删除文件: {object_name}")
            except Exception as e:
                logger.warning(f"从 MinIO 删除文件失败 (可能文件不存在): {str(e)}")

            # 2. 尝试从本地文件系统删除 (如果存在)
            local_path = Path(file_path)
            if not local_path.is_absolute():
                local_path = Path(settings.UPLOAD_DIR) / file_path
                
            if local_path.exists() and local_path.is_file():
                os.remove(local_path)
                logger.info(f"已从本地文件系统删除文件: {local_path}")
            
            return True
        except Exception as e:
            logger.error(f"删除文件失败: {str(e)}")
            return False
    async def upload_file(
        self, 
        file: UploadFile, 
        organization_id: str, 
        user_id: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        上传文件（完整文件上传）
        
        Args:
            file: 上传的文件
            organization_id: 组织ID
            user_id: 用户ID
            description: 文件描述
            tags: 标签列表
            
        Returns:
            上传结果
        """
        try:
            # 验证文件
            filename = file.filename
            if not filename:
                raise HTTPException(status_code=400, detail="文件名不能为空")
            
            # 读取文件内容
            content = await file.read()
            file_size = len(content)
            
            # 验证文件
            self._validate_file(filename, file_size)
            
            # 生成文件哈希
            file_hash = self._generate_file_hash(content)
            
            # 检查文件是否已存在（通过哈希值）
            async with AsyncSessionLocal() as session:
                existing_doc = await session.execute(
                    select(Document).where(
                        and_(
                            Document.organization_id == organization_id,
                            # 这里可以添加文件哈希检查逻辑
                        )
                    )
                )
                # 如果文件已存在，可以选择直接返回或更新
            
            # 生成唯一文件名
            file_extension = Path(filename).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            
            # 上传到MinIO
            object_name = f"documents/{organization_id}/{unique_filename}"
            file_url = await self._upload_to_minio(content, object_name, file.content_type or "application/octet-stream")
            
            # 创建文档记录
            document = Document(
                id=str(uuid.uuid4()),
                filename=filename,
                file_path=file_url,
                file_size=file_size,
                file_type=self._get_document_type(file_extension),
                mime_type=file.content_type,
                description=description,
                organization_id=organization_id,
                uploaded_by=user_id,
                status=DocumentStatus.UPLOADED
            )
            
            # 保存到数据库
            async with AsyncSessionLocal() as session:
                session.add(document)
                await session.commit()
                await session.refresh(document)
            
            # 异步处理文档解析
            asyncio.create_task(self._process_document_async(document.id))
            
            logger.info(f"文件上传成功: {filename}, 文档ID: {document.id}")
            
            return {
                "document_id": document.id,
                "filename": filename,
                "file_size": file_size,
                "status": document.status.value,
                "upload_time": document.created_at.isoformat()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"文件上传失败: {filename}, 错误: {str(e)}")
            raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")

    async def upload_avatar(
        self,
        file: UploadFile,
        user_id: int
    ) -> str:
        """
        上传用户头像
        
        Args:
            file: 上传的文件
            user_id: 用户ID
            
        Returns:
            头像URL
        """
        try:
            # 验证文件
            filename = file.filename
            if not filename:
                raise HTTPException(status_code=400, detail="文件名不能为空")
            
            # 读取文件内容
            content = await file.read()
            file_size = len(content)
            
            # 验证文件 (is_avatar=True)
            self._validate_file(filename, file_size, is_avatar=True)
            
            # 生成唯一文件名
            file_extension = Path(filename).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            
            # 上传到MinIO (avatars bucket or folder)
            object_name = f"avatars/{user_id}/{unique_filename}"
            file_url = await self._upload_to_minio(content, object_name, file.content_type or "image/jpeg")
            
            return file_url
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"头像上传失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"头像上传失败: {str(e)}")
    
    async def upload_chunk(
        self,
        chunk_data: bytes,
        chunk_index: int,
        total_chunks: int,
        file_name: str,
        file_hash: str,
        organization_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        上传文件分块（用于大文件断点续传）
        
        Args:
            chunk_data: 分块数据
            chunk_index: 分块序号
            total_chunks: 总分块数
            file_name: 文件名
            file_hash: 文件哈希
            organization_id: 组织ID
            user_id: 用户ID
            
        Returns:
            上传结果
        """
        try:
            # 验证分块数据
            if not chunk_data or len(chunk_data) == 0:
                raise HTTPException(status_code=400, detail="分块数据不能为空")
            
            if chunk_index < 0 or chunk_index >= total_chunks:
                raise HTTPException(status_code=400, detail="分块序号无效")
            
            # 创建临时目录存储分块
            temp_dir = Path(settings.UPLOAD_TEMP_DIR) / file_hash
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # 保存分块文件
            chunk_file_path = temp_dir / f"chunk_{chunk_index}"
            with open(chunk_file_path, 'wb') as f:
                f.write(chunk_data)
            
            # 记录分块上传状态到Redis（用于断点续传）
            # 这里简化处理，实际项目中应该使用Redis存储上传状态
            
            logger.info(f"文件分块上传成功: {file_name}, 分块 {chunk_index + 1}/{total_chunks}")
            
            # 检查是否所有分块都已上传完成
            if await self._check_all_chunks_uploaded(temp_dir, total_chunks):
                # 合并分块
                merged_file_path = await self._merge_chunks(temp_dir, total_chunks, file_name)
                
                # 处理完整文件
                result = await self._process_merged_file(
                    merged_file_path, file_name, organization_id, user_id
                )
                
                # 清理临时文件
                await self._cleanup_temp_files(temp_dir)
                
                return {
                    "status": "completed",
                    "document_id": result["document_id"],
                    "message": "文件上传完成"
                }
            else:
                return {
                    "status": "chunk_uploaded",
                    "chunk_index": chunk_index,
                    "message": f"分块 {chunk_index + 1}/{total_chunks} 上传成功"
                }
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"分块上传失败: {file_name}, 分块 {chunk_index}, 错误: {str(e)}")
            raise HTTPException(status_code=500, detail=f"分块上传失败: {str(e)}")
    
    async def _check_all_chunks_uploaded(self, temp_dir: Path, total_chunks: int) -> bool:
        """检查是否所有分块都已上传"""
        for i in range(total_chunks):
            chunk_file = temp_dir / f"chunk_{i}"
            if not chunk_file.exists():
                return False
        return True
    
    async def _merge_chunks(self, temp_dir: Path, total_chunks: int, file_name: str) -> Path:
        """合并分块文件"""
        try:
            # 创建合并后的文件
            merged_file_path = temp_dir / f"merged_{file_name}"
            
            with open(merged_file_path, 'wb') as merged_file:
                for i in range(total_chunks):
                    chunk_file_path = temp_dir / f"chunk_{i}"
                    with open(chunk_file_path, 'rb') as chunk_file:
                        merged_file.write(chunk_file.read())
            
            logger.info(f"文件分块合并完成: {file_name}")
            return merged_file_path
            
        except Exception as e:
            logger.error(f"分块合并失败: {file_name}, 错误: {str(e)}")
            raise Exception(f"分块合并失败: {str(e)}")
    
    async def _process_merged_file(
        self, 
        merged_file_path: Path, 
        file_name: str, 
        organization_id: str, 
        user_id: str
    ) -> Dict[str, Any]:
        """处理合并后的完整文件"""
        try:
            # 读取合并后的文件
            with open(merged_file_path, 'rb') as f:
                file_content = f.read()
            
            file_size = len(file_content)
            
            # 验证文件
            self._validate_file(file_name, file_size)
            
            # 生成唯一文件名
            file_extension = Path(file_name).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            
            # 上传到MinIO
            object_name = f"documents/{organization_id}/{unique_filename}"
            
            # 模拟UploadFile对象
            class MockUploadFile:
                def __init__(self, filename, content, content_type):
                    self.filename = filename
                    self.content = content
                    self.content_type = content_type
                
                async def read(self):
                    return self.content
            
            mock_file = MockUploadFile(file_name, file_content, self._get_content_type(file_extension))
            
            # 调用普通上传方法
            return await self.upload_file(mock_file, organization_id, user_id)
            
        except Exception as e:
            logger.error(f"合并文件处理失败: {file_name}, 错误: {str(e)}")
            raise Exception(f"合并文件处理失败: {str(e)}")
    
    async def _cleanup_temp_files(self, temp_dir: Path) -> None:
        """清理临时文件"""
        try:
            import shutil
            shutil.rmtree(temp_dir)
            logger.info(f"临时文件清理完成: {temp_dir}")
        except Exception as e:
            logger.warning(f"临时文件清理失败: {temp_dir}, 错误: {str(e)}")
    
    def _get_document_type(self, file_extension: str) -> DocumentType:
        """根据文件扩展名获取文档类型"""
        extension_map = {
            '.pdf': DocumentType.PDF,
            '.doc': DocumentType.WORD,
            '.docx': DocumentType.WORD,
            '.xls': DocumentType.EXCEL,
            '.xlsx': DocumentType.EXCEL,
            '.ppt': DocumentType.PPT,
            '.pptx': DocumentType.PPT,
            '.txt': DocumentType.TXT,
            '.md': DocumentType.TXT,
            '.csv': DocumentType.TXT,
        }
        return extension_map.get(file_extension, DocumentType.OTHER)
    
    def _get_content_type(self, file_extension: str) -> str:
        """根据文件扩展名获取内容类型"""
        content_type_map = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.ppt': 'application/vnd.ms-powerpoint',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.csv': 'text/csv',
        }
        return content_type_map.get(file_extension, 'application/octet-stream')
    
    async def _process_document_async(self, document_id: str) -> None:
        """异步处理文档解析"""
        try:
            logger.info(f"开始异步处理文档: {document_id}")
            success = await self.document_service.process_document(document_id)
            
            if success:
                logger.info(f"文档解析成功，开始构建知识库索引: {document_id}")
                # 局部导入避免循环依赖
                from app.services.knowledge_service import knowledge_service
                await knowledge_service.build_knowledge_base(document_id)
                logger.info(f"文档全流程处理完成: {document_id}")
            else:
                logger.error(f"文档解析失败: {document_id}")

        except Exception as e:
            logger.error(f"文档异步处理失败: {document_id}, 错误: {str(e)}")
    
    async def get_upload_status(self, file_hash: str) -> Dict[str, Any]:
        """
        获取文件上传状态（用于断点续传）
        
        Args:
            file_hash: 文件哈希值
            
        Returns:
            上传状态信息
        """
        try:
            temp_dir = Path(settings.UPLOAD_TEMP_DIR) / file_hash
            
            if not temp_dir.exists():
                return {
                    "status": "not_started",
                    "uploaded_chunks": 0,
                    "message": "上传未开始"
                }
            
            # 统计已上传的分块
            uploaded_chunks = 0
            chunk_files = list(temp_dir.glob("chunk_*"))
            
            for chunk_file in chunk_files:
                try:
                    chunk_index = int(chunk_file.name.replace("chunk_", ""))
                    uploaded_chunks += 1
                except ValueError:
                    continue
            
            return {
                "status": "in_progress" if uploaded_chunks > 0 else "not_started",
                "uploaded_chunks": uploaded_chunks,
                "message": f"已上传 {uploaded_chunks} 个分块"
            }
            
        except Exception as e:
            logger.error(f"获取上传状态失败: {file_hash}, 错误: {str(e)}")
            return {
                "status": "error",
                "message": f"获取状态失败: {str(e)}"
            }
    
    async def get_document_list(
        self, 
        organization_id: str, 
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        获取文档列表
        
        Args:
            organization_id: 组织ID
            user_id: 用户ID（可选，用于筛选特定用户上传的文档）
            status: 文档状态（可选）
            skip: 跳过数量
            limit: 返回数量限制
            
        Returns:
            文档列表和统计信息
        """
        try:
            async with AsyncSessionLocal() as session:
                # 构建查询条件
                query = select(Document).where(Document.organization_id == organization_id)
                
                if user_id:
                    query = query.where(Document.uploaded_by == user_id)
                
                if status:
                    query = query.where(Document.status == status)
                
                # 获取总数
                total_query = select(func.count()).select_from(query.subquery())
                total_result = await session.execute(total_query)
                total = total_result.scalar()
                
                # 获取文档列表
                query = query.order_by(Document.created_at.desc()).offset(skip).limit(limit)
                result = await session.execute(query)
                documents = result.scalars().all()
                
                # 转换为字典格式
                document_list = []
                for doc in documents:
                    document_list.append({
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_size": doc.file_size,
                        "file_type": doc.file_type.value,
                        "status": doc.status.value,
                        "description": doc.description,
                        "content_length": doc.content_length,
                        "chunk_count": doc.chunk_count,
                        "created_at": doc.created_at.isoformat(),
                        "parsed_at": doc.parsed_at.isoformat() if doc.parsed_at else None,
                        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None
                    })
                
                return {
                    "total": total,
                    "documents": document_list,
                    "skip": skip,
                    "limit": limit
                }
                
        except Exception as e:
            logger.error(f"获取文档列表失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"获取文档列表失败: {str(e)}")
    
    async def delete_document(self, document_id: str, organization_id: str, user_id: str) -> bool:
        """
        删除文档
        
        Args:
            document_id: 文档ID
            organization_id: 组织ID
            user_id: 用户ID
            
        Returns:
            是否删除成功
        """
        try:
            async with AsyncSessionLocal() as session:
                # 获取文档
                result = await session.execute(
                    select(Document).where(
                        and_(
                            Document.id == document_id,
                            Document.organization_id == organization_id
                        )
                    )
                )
                document = result.scalar_one_or_none()
                
                if not document:
                    raise HTTPException(status_code=404, detail="文档不存在")
                
                # 检查权限（只有上传者或管理员可以删除）
                # 这里简化处理，实际项目中需要更完善的权限检查
                
                # 从MinIO删除文件
                try:
                    minio_client = get_minio_client()
                    # 从文件路径中提取对象名称
                    object_name = document.file_path.split('/')[-1]
                    minio_client.remove_object(settings.MINIO_BUCKET_NAME, object_name)
                except Exception as e:
                    logger.warning(f"从MinIO删除文件失败: {document_id}, 错误: {str(e)}")
                
                # 从数据库删除文档记录（级联删除分块）
                await session.delete(document)
                await session.commit()
                
                logger.info(f"文档删除成功: {document_id}")
                return True
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"删除文档失败: {document_id}, 错误: {str(e)}")
            raise HTTPException(status_code=500, detail=f"删除文档失败: {str(e)}")


# 创建服务实例
file_service = FileUploadService()