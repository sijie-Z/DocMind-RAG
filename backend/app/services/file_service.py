"""
文件服务 - 处理文件上传、存储和管理
"""
import asyncio
import hashlib
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import and_, func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.minio_client import minio_client
from app.exceptions import AppError, ValidationError
from app.models.document import Document, DocumentStatus, DocumentType
from app.services.document_parser import DocumentParser

logger = logging.getLogger(__name__)

# 持有后台任务引用，避免 asyncio 在任务完成前将其当作垃圾回收
_background_tasks: set[asyncio.Task] = set()

# MIME type → allowed extensions mapping (magic-byte validated)
_ALLOWED_MIME_TYPES = {
    "application/pdf": {".pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.ms-excel": {".xls"},
    "text/plain": {".txt", ".md", ".csv"},
    "text/markdown": {".md"},
    "text/csv": {".csv"},
    # 图片（round2 后端卫生修复②）：解析链已接图片（document_parser 按扩展名
    # 分发 _parse_image，OCR 支线）；/files/upload 与分块上传的白名单同步放行，
    # 与主端点 /documents/upload（本就无类型闸门）口径一致。mime 串为 filetype
    # 库实测值（image/png|jpeg|gif|webp|bmp）。
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "image/bmp": {".bmp"},
}

# 头像专用 MIME 表（媒体审计 §3.4：头像扩展名白名单已放行图片，但当时共用的
# 文档 MIME 表无 image/* 导致真图片必 500）。覆盖前端 accept="image/*" 的主要
# 格式，与 allowed_avatar_extensions 一一对应。round2 卫生修复②后文档表同样
# 放行图片（解析链已接 OCR）——此表保留的差异是**收窄**：头像路径只收图片，
# 不收文档格式。
_ALLOWED_AVATAR_MIME_TYPES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
}


class FileUploadService:
    """文件上传服务 - 支持大文件分块上传"""

    def __init__(self):
        # 分块上限读配置（issue #242 接线）：默认 5MB，与接线前硬编码同值；
        # 该值是服务端接收分块的合法上限与总块数派生基准（upload_chunk）。
        self.chunk_size = settings.CHUNK_SIZE
        self.allowed_extensions = {
            '.pdf', '.docx', '.xlsx', '.xls', '.txt', '.md', '.csv',
            # 图片（round2 后端卫生修复②）：OCR 支线解析链已支持，
            # 与主端点 /documents/upload 口径对齐
            '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
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

    def _validate_mime_type(self, content: bytes, filename: str, is_avatar: bool = False) -> None:
        """Validate file content MIME type against magic bytes.

        Raises ValidationError if the detected MIME type doesn't match the file extension
        or is not in the allowed list. ``is_avatar=True`` 时使用头像专用 MIME 表
        （只收 image/png|jpeg|gif|webp）；文档路径使用文档表（round2 卫生修复②
        起同样含 image/png|jpeg|gif|webp|bmp）。
        """
        try:
            import filetype
            kind = filetype.guess(content)
            if kind is None:
                # filetype can't detect — fall back to allowing text-like extensions
                ext = Path(filename).suffix.lower()
                text_extensions = {".txt", ".md", ".csv", ".json", ".xml", ".html"}
                if ext not in text_extensions:
                    raise ValidationError(
                        message=f"Unable to verify file type for '{filename}'. "
                        f"If this is a text file, rename to .txt or .md."
                    )
                return

            detected_mime = kind.mime
            ext = Path(filename).suffix.lower()

            allowed_map = _ALLOWED_AVATAR_MIME_TYPES if is_avatar else _ALLOWED_MIME_TYPES
            allowed_exts = allowed_map.get(detected_mime)
            if allowed_exts is None:
                raise ValidationError(
                    message=f"File content type '{detected_mime}' is not allowed."
                )
            if ext not in allowed_exts:
                raise ValidationError(
                    message=f"File extension '{ext}' doesn't match detected type '{detected_mime}'. "
                    f"Expected one of: {', '.join(sorted(allowed_exts))}"
                )
        except (ValidationError):
            raise
        except Exception as e:
            logger.warning(f"MIME type validation error (allowing file): {e}")

    def _generate_file_hash(self, content: bytes) -> str:
        """
        生成文件内容的哈希值
        
        Args:
            content: 文件内容
            
        Returns:
            文件哈希值
        """
        return hashlib.sha256(content).hexdigest()

    def _sanitize_upload_identity(
        self, file_name: str | None, file_hash: str | None
    ) -> tuple[str, str]:
        """Validate client-supplied upload identity before using it in paths."""
        if not file_name or not file_hash:
            raise HTTPException(status_code=400, detail="文件名和文件哈希不能为空")
        if len(file_name) > 255:
            raise HTTPException(status_code=400, detail="文件名过长")
        if Path(file_name).name != file_name or "/" in file_name or "\\" in file_name:
            raise HTTPException(status_code=400, detail="文件名不合法")
        if len(file_hash) != 64 or any(c not in "0123456789abcdefABCDEF" for c in file_hash):
            raise HTTPException(status_code=400, detail="文件哈希不合法")
        return file_name, file_hash.lower()

    async def _upload_to_minio(self, file_content: bytes, object_name: str, content_type: str) -> str:
        """
        上传文件到MinIO
        
        Args:
            file_content: 文件内容
            object_name: 对象名称
            content_type: 内容类型
            
        Returns:
            对象名称（统一存储规范：file_path 只存对象名，不存浏览器不可达的内网 URL；
            下载一律走 /files 代理或 presigned URL）
        """
        try:
            # 上传文件
            from io import BytesIO
            minio_client.put_object(
                object_name=object_name,
                data=BytesIO(file_content),
                length=len(file_content),
                content_type=content_type,
            )
            return object_name

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
                # 统一解析：兼容对象名与历史 URL 两种存储格式
                from app.core.minio_client import normalize_object_name
                object_name = normalize_object_name(file_path)

                minio_client.remove_object(object_name)
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
        description: str | None = None,
        tags: list[str] | None = None
    ) -> dict[str, Any]:
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

            # 验证文件（扩展名 + 大小 + MIME type）
            self._validate_file(filename, file_size)
            self._validate_mime_type(content, filename)

            # 生成文件哈希
            self._generate_file_hash(content)

            # 检查文件是否已存在（通过哈希值）
            async with AsyncSessionLocal() as session:
                await session.execute(
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

            # A3-P0：先落 DB 标记（local_fallback=True 的 QUEUED job），再调度
            # 进程内任务；进程若在任务完成前退出，启动对账按标记回收。
            job_id = await self._ensure_local_job(document)
            task = asyncio.create_task(
                self._process_document_async(document.id, job_id=job_id)
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            logger.info(f"文件上传成功: {filename}, 文档ID: {document.id}")

            return {
                "document_id": document.id,
                "filename": filename,
                "file_size": file_size,
                "status": document.status.value,
                "upload_time": document.created_at.isoformat()
            }

        except (HTTPException, AppError):
            # 校验类失败（扩展名/大小 → HTTPException 400；MIME 不符 →
            # ValidationError 400）与其它控制流异常原样放行，不得被下面的
            # except Exception 重包成 500（round2 后端卫生修复③；issue #98
            # 同款原则，见 tests/unit/test_control_flow_exception_passthrough.py）。
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
            self._validate_mime_type(content, filename, is_avatar=True)

            # 生成唯一文件名
            file_extension = Path(filename).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_extension}"

            # 上传到MinIO (avatars bucket or folder)
            object_name = f"avatars/{user_id}/{unique_filename}"
            return await self._upload_to_minio(content, object_name, file.content_type or "image/jpeg")


        except (HTTPException, AppError):
            # 与 upload_file 同款：校验类失败必须诚实 4xx（round2 卫生修复③）
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
    ) -> dict[str, Any]:
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

            # 单块大小不得超过 chunk_size（5MB），防止恶意超大分块写入磁盘
            if len(chunk_data) > self.chunk_size:
                raise HTTPException(
                    status_code=400,
                    detail=f"分块数据超过大小限制（最大 {self.chunk_size // (1024 * 1024)}MB）"
                )

            # 总分块数上限 = ceil(max_file_size / chunk_size) + 1（100MB 对应 21），防止磁盘耗尽
            max_total_chunks = math.ceil(self.max_file_size / self.chunk_size) + 1
            if total_chunks > max_total_chunks:
                raise HTTPException(
                    status_code=400,
                    detail=f"总分块数超过限制（最大 {max_total_chunks} 个）"
                )

            if chunk_index < 0 or chunk_index >= total_chunks:
                raise HTTPException(status_code=400, detail="分块序号无效")

            file_name, file_hash = self._sanitize_upload_identity(file_name, file_hash)

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
            return {
                "status": "chunk_uploaded",
                "chunk_index": chunk_index,
                "message": f"分块 {chunk_index + 1}/{total_chunks} 上传成功"
            }

        except (HTTPException, AppError):
            # 与 upload_file 同款：校验类失败必须诚实 4xx（round2 卫生修复③）
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
            file_name, _ = self._sanitize_upload_identity(file_name, "0" * 64)
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
    ) -> dict[str, Any]:
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
            f"{uuid.uuid4()}{file_extension}"

            # 上传到MinIO

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

        except (HTTPException, AppError):
            # 合并阶段复用的校验（扩展名/大小/MIME）失败必须原样上抛——
            # 此前统一重包成裸 Exception，把上游的 400 语义打碎成 500
            # （round2 后端卫生修复③）。
            raise
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
            # 图片（round2 卫生修复②）：分块合并路径经此派生 MockUploadFile 的
            # content_type，落 MinIO 时不再退化成 application/octet-stream
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp',
        }
        return content_type_map.get(file_extension, 'application/octet-stream')

    async def _ensure_local_job(self, document) -> int | None:
        """先落本机处理标记（A3-P0）：进程重启后启动对账按此回收任务。

        落库失败返回 None（退回由 ``_process_document_async`` 自建 job 的兼容路径）。
        """
        from app.models.knowledge_job import (
            KnowledgeJobStatus,
            KnowledgeProcessingJob,
        )

        try:
            async with AsyncSessionLocal() as session:
                job = KnowledgeProcessingJob(
                    document_id=document.id,
                    organization_id=document.organization_id,
                    trigger_type="upload",
                    status=KnowledgeJobStatus.QUEUED,
                    retry_count=0,
                    local_fallback=True,
                )
                session.add(job)
                await session.commit()
                return job.id
        except Exception as e:
            logger.error(
                f"本机处理标记落库失败，退回兼容调度: {document.id}, 错误: {str(e)}",
                exc_info=True,
            )
            return None

    async def _process_document_async(
        self, document_id: str, job_id: int | None = None
    ) -> None:
        """异步处理文档解析。

        ``job_id`` 由 ``upload_file`` 预先落库（local_fallback 标记）后传入；
        缺省时自建 job 并同样标记 ``local_fallback=True``（兼容直接调用/旧路径）。

        issue #301 批 D（#29）：此前本壳的 except 只留一条 error 日志——job 创建
        窗口 DB 不可用等异常被静默吞掉后，文档永久卡在 UPLOADED（无 FAILED、无
        job 行、无重试入口）。现在：异常必显形（ERROR + exc_info）并**尽力**把
        文档状态推进为 FAILED（带原因）；兜底标记自身失败时响亮记录（critical），
        绝不再次静默。
        """
        try:
            logger.info(f"开始异步处理文档: {document_id}")
            from app.models.knowledge_job import (
                KnowledgeJobStatus,
                KnowledgeProcessingJob,
            )

            if job_id is None:
                async with AsyncSessionLocal() as session:
                    doc = await session.get(Document, document_id)
                    if not doc:
                        logger.error(f"文档不存在，无法创建处理任务: {document_id}")
                        return
                    job = KnowledgeProcessingJob(
                        document_id=document_id,
                        organization_id=doc.organization_id,
                        trigger_type="upload",
                        status=KnowledgeJobStatus.QUEUED,
                        local_fallback=True,
                    )
                    session.add(job)
                    await session.commit()
                    job_id = job.id

            from app.worker.doc_processor import DEFERRED, processor

            success = await processor.process(document_id, job_id)
            if success is DEFERRED:
                logger.info(f"文档处理延后（另一处理者持锁）: {document_id}")
            elif success is False:
                logger.error(f"文档解析失败: {document_id}")
            else:
                logger.info(f"文档全流程处理完成: {document_id}")

        except Exception as e:
            logger.error(f"文档异步处理失败: {document_id}, 错误: {str(e)}", exc_info=True)
            await self._mark_document_failed_best_effort(document_id, e)

    async def _mark_document_failed_best_effort(
        self, document_id: str, error: Exception
    ) -> None:
        """兜底路径：尽力把文档推进为 FAILED（issue #301 批 D / #29）。

        单独开 session（异常可能就发生在原连接/session 上）。已 INDEXED 的文档
        不因晚到的兜底异常被改写（无法归因的异常宁可不写状态）；本方法自身失败
        只允许响亮记录（critical）——"文档可能卡在 uploaded"这一事实必须留痕。
        """
        try:
            async with AsyncSessionLocal() as session:
                doc = await session.get(Document, document_id)
                if doc is None:
                    logger.error(f"无法标记失败：文档不存在 {document_id}")
                    return
                if doc.status == DocumentStatus.INDEXED:
                    logger.error(
                        f"文档 {document_id} 已 INDEXED，跳过兜底失败标记"
                        f"（原异常: {error}）"
                    )
                    return
                doc.status = DocumentStatus.FAILED
                doc.parse_error = f"异步处理失败: {str(error)}"
                await session.commit()
                logger.info(f"文档 {document_id} 已标记为 FAILED（兜底路径）")
        except Exception as mark_err:
            logger.critical(
                f"文档 {document_id} 异步处理失败，且 FAILED 状态写入也失败"
                f"（文档可能卡在 uploaded）: {mark_err}",
                exc_info=True,
            )

    async def get_upload_status(self, file_hash: str) -> dict[str, Any]:
        """
        获取文件上传状态（用于断点续传）
        
        Args:
            file_hash: 文件哈希值
            
        Returns:
            上传状态信息
        """
        try:
            if not file_hash or len(file_hash) != 64 or any(
                c not in "0123456789abcdefABCDEF" for c in file_hash
            ):
                raise HTTPException(status_code=400, detail="文件哈希不合法")
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
                    int(chunk_file.name.replace("chunk_", ""))
                    uploaded_chunks += 1
                except ValueError:
                    continue

            return {
                "status": "in_progress" if uploaded_chunks > 0 else "not_started",
                "uploaded_chunks": uploaded_chunks,
                "message": f"已上传 {uploaded_chunks} 个分块"
            }

        except HTTPException:
            # issue #98：非法哈希本应 400，不能被下面的 except Exception 吞成 200 错误字典。
            raise
        except Exception as e:
            logger.error(f"获取上传状态失败: {file_hash}, 错误: {str(e)}")
            return {
                "status": "error",
                "message": f"获取状态失败: {str(e)}"
            }

    async def get_document_list(
        self,
        organization_id: str,
        user_id: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 20
    ) -> dict[str, Any]:
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
        """删除文档（兼容包装 —— 已并入 knowledge 统一删除链路，#416）。

        旧实现只删 ``documents`` 主表：MySQL 下会被 ``document_tags`` /
        ``knowledge_processing_jobs`` 的 NO ACTION 外键挡住（1451），且不清
        ES 索引与 RAG 缓存。现委托
        ``knowledge_service.delete_knowledge_thoroughly`` —— 与
        ``DELETE /api/v1/knowledge/document/{id}`` 同一条链路；本签名保留供
        历史调用方，文档不存在按幂等成功处理（与统一链路一致）。

        ``user_id`` 仅为历史签名保留（旧实现同样未做上传者校验）。
        """
        # 局部 import：knowledge_service 在模块顶层 import 本模块的
        # FileUploadService，顶层互相 import 会成环。
        from app.services.knowledge_service import knowledge_service

        try:
            org_id: int | None = int(organization_id)
        except (TypeError, ValueError):
            # 旧实现的 org 过滤对非法/缺失组织恒不匹配 → 404；保持一致。
            raise HTTPException(status_code=404, detail="文档不存在") from None

        return await knowledge_service.delete_knowledge_thoroughly(document_id, org_id)


# 创建服务实例
file_service = FileUploadService()
