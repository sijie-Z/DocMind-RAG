# backend/app/api/v1/endpoints/documents.py

import hashlib
import uuid
import json
import logging
import asyncio
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.user import User
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob, KnowledgeJobStatus
from app.core.security import get_current_user, permission_required
from app.core.minio_client import minio_client
from app.core.kafka_client import kafka_producer
from app.core.config import settings
from app.models.rbac import PermissionType

router = APIRouter()
logger = logging.getLogger(__name__)

def get_document_type(filename: str) -> DocumentType:
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return DocumentType.PDF
    elif ext in ['doc', 'docx']:
        return DocumentType.WORD
    elif ext in ['xls', 'xlsx']:
        return DocumentType.EXCEL
    elif ext in ['ppt', 'pptx']:
        return DocumentType.PPT
    elif ext == 'txt':
        return DocumentType.TXT
    else:
        return DocumentType.OTHER

async def calculate_md5(file: UploadFile) -> str:
    md5_hash = hashlib.md5()
    while chunk := await file.read(8192):
        md5_hash.update(chunk)
    await file.seek(0)
    return md5_hash.hexdigest()

def build_brief_summary(content: str) -> str:
    parts = [line.strip() for line in content.splitlines() if line.strip()]
    if not parts:
        return "暂无可用简介"
    focus_words = ["计划", "安排", "课程", "学习", "出行", "旅游", "实践", "时间", "目标", "注意"]
    noise_words = ["比赛", "竞赛", "宣传", "广告", "赞助", "规则说明"]
    scored_parts = []
    for idx, part in enumerate(parts):
        score = 0
        for w in focus_words:
            if w in part:
                score += 2
        for w in noise_words:
            if w in part:
                score -= 1
        if len(part) > 180:
            score -= 1
        scored_parts.append((score, idx, part))
    scored_parts.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    picked = [p for s, _, p in scored_parts if s > 0][:5]
    if len(picked) < 3:
        picked.extend(parts[-3:])
    merged = []
    for p in picked:
        if p not in merged:
            merged.append(p)
        if len(merged) >= 5:
            break
    summary = "；".join(merged)
    return summary[:420]


def build_suggested_tags(content: str) -> List[str]:
    text = (content or "").strip()
    if not text:
        return []
    tag_rules = [
        ("每日计划", ["计划", "日程", "安排", "时间表"]),
        ("课程学习", ["课程", "学习", "复习", "训练"]),
        ("出行游玩", ["旅游", "出行", "游玩", "景点"]),
        ("竞赛相关", ["比赛", "竞赛", "算法赛", "赛程"]),
        ("执行清单", ["任务", "清单", "待办", "目标"]),
    ]
    tags: List[str] = []
    for tag, words in tag_rules:
        if any(w in text for w in words):
            tags.append(tag)
    return tags[:6]

@router.post("/upload", status_code=status.HTTP_201_CREATED, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_UPLOAD], organization_id_param='organization_id'))])
async def upload_document(
    file: UploadFile = File(...),
    organization_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    上传文档并存入 MinIO 和 数据库
    """
    try:
        # 1. 核心修复：合并用户 Session 防止 Persistent 错误
        current_user = await db.merge(current_user)

        # 2. 确定组织 ID
        org_id = 1
        if organization_id and organization_id.strip() and organization_id != 'undefined':
            try:
                org_id = int(organization_id)
            except:
                org_id = current_user.organization_id or 1
        else:
            org_id = current_user.organization_id or 1
        
        # 3. 解析标签为列表
        keywords_list = []
        if tags:
            try:
                # 前端可能传 JSON 字符串 ["tag1", "tag2"]
                keywords_list = json.loads(tags)
            except:
                # 也可能传普通逗号分隔字符串 "tag1,tag2"
                keywords_list = [t.strip() for t in tags.split(",") if t.strip()]

        # 4. 计算 MD5 和 文件大小
        md5 = await calculate_md5(file)
        file.file.seek(0, 2)
        file_size = file.file.tell()
        await file.seek(0)
        
        file_ext = file.filename.split('.')[-1] if '.' in file.filename else 'bin'  # pyright: ignore[reportOperatorIssue]
        object_name = f"{org_id}/{md5}.{file_ext}"
        
        # 5. 上传到 MinIO
        try:
            # 异步线程执行同步的 minio 客户端方法
            await asyncio.to_thread(minio_client.stat_object, object_name)
            logger.info(f"File {object_name} already exists, skipping upload.")
        except:
            await asyncio.to_thread(
                minio_client.put_object,
                object_name=object_name,
                data=file.file,
                length=file_size,
                content_type=file.content_type or "application/octet-stream"
            )
        
        # 6. 写入数据库 (核心修复点：tags -> keywords)
        doc_id = str(uuid.uuid4())
        new_doc = Document(
            id=doc_id,
            filename=file.filename,
            file_path=object_name,
            file_size=file_size,
            file_type=get_document_type(file.filename),
            md5_hash=md5,
            status=DocumentStatus.PENDING,
            organization_id=org_id,
            uploaded_by=current_user.id,
            title=title or file.filename,
            description=description,
            keywords=keywords_list  # ✅ 已修正字段名！
        )
        
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)
        
        # 7. 创建任务并发送 Kafka 消息
        job = KnowledgeProcessingJob(
            document_id=doc_id,
            organization_id=org_id,
            trigger_type="upload",
            status=KnowledgeJobStatus.QUEUED,
            retry_count=0,
        )
        db.add(job)

        try:
            message = {
                "document_id": doc_id,
                "organization_id": org_id,
                "file_path": object_name,
                "filename": file.filename,
                "file_type": new_doc.file_type.value if hasattr(new_doc.file_type, "value") else str(new_doc.file_type),
                "action": "process"
            }
            await kafka_producer.send_message(settings.KAFKA_FILE_PROCESSING_TOPIC, message)
        except Exception as kafka_err:
            logger.error(f"Kafka send failed: {kafka_err}")
            setattr(job, "status", KnowledgeJobStatus.FAILED)
            setattr(job, "error_message", f"Kafka send failed: {str(kafka_err)}")
            setattr(job, "finished_at", datetime.now())
        
        # 8. 返回，防止 refresh 导致的懒加载报错，使用 to_dict
        return {
            "success": True,
            "message": "上传成功",
            "data": {
                "id": new_doc.id,
                "filename": new_doc.filename,
                "title": new_doc.title,
                "status": new_doc.status.value if hasattr(new_doc.status, 'value') else str(new_doc.status)
            }
        }
        
    except Exception as e:
        logger.error(f"Upload failed trace: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"上传失败: {str(e)}"
        )

@router.get("/{document_id}", dependencies=[Depends(get_current_user)])
async def get_document_status(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取单个文档状态及详情
    """
    try:
        doc = await db.get(Document, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")

        # 鉴权：只能查看自己上传的或同组织的
        if doc.organization_id != current_user.organization_id and doc.uploaded_by != current_user.id:
            raise HTTPException(status_code=403, detail="无权查看该文档")

        # 状态映射：将内部状态转换为前端期望的状态
        status_map = {
            DocumentStatus.PENDING: "pending",
            DocumentStatus.UPLOADED: "uploaded",
            DocumentStatus.PARSING: "parsing",
            DocumentStatus.PARSED: "parsed",
            DocumentStatus.INDEXED: "indexed",
            DocumentStatus.FAILED: "failed"
        }
        raw_status = doc.status.value if hasattr(doc.status, 'value') else str(doc.status)
        display_status = status_map.get(doc.status, raw_status) if isinstance(doc.status, str) else status_map.get(raw_status, raw_status)

        return {
            "success": True,
            "data": {
                "id": doc.id,
                "title": doc.title or doc.filename,
                "filename": doc.filename,
                "file_size": doc.file_size,
                "file_type": doc.file_type.value if hasattr(doc.file_type, 'value') else str(doc.file_type),
                "status": display_status,
                "raw_status": raw_status,
                "parse_error": doc.parse_error,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                "parsed_at": doc.parsed_at.isoformat() if doc.parsed_at else None,
                "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
                "chunk_count": doc.chunk_count,
                "description": doc.description,
                "keywords": doc.keywords or [],
                "upload_source": "聊天上传" if (doc.description and ("来自聊天" in doc.description or "uploaded from chat" in doc.description.lower())) else "知识库上传"
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get document failed: {str(e)}")
        raise HTTPException(status_code=500, detail="获取文档状态失败")

@router.get("/{document_id}/content", dependencies=[Depends(get_current_user)])
async def get_document_full_content(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取文档全文内容（用于预览）
    """
    try:
        doc = await db.get(Document, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")
            
        # 鉴权
        if doc.organization_id != current_user.organization_id and doc.uploaded_by != current_user.id:
            raise HTTPException(status_code=403, detail="无权查看该文档内容")
            
        # 从 ES 中获取该文档的所有分块并按 index 排序
        from app.core.elasticsearch import ElasticsearchTools
        query = {
            "query": {
                "term": {"document_id": document_id}
            },
            "size": 1000,
            "sort": [{"metadata.chunk_index": {"order": "asc"}}]
        }
        res = await ElasticsearchTools.search_documents(query)
        hits = res.get("hits", {}).get("hits", [])
        
        full_text = ""
        if hits:
            full_text = "\n".join([h.get("_source", {}).get("content", "") for h in hits])
        
        # 如果 ES 没找到，尝试从数据库 chunks 表找 (备用)
        if not full_text:
            from app.models.document import DocumentChunk
            stmt = select(DocumentChunk).where(DocumentChunk.document_id == document_id).order_by(DocumentChunk.chunk_index.asc())
            chunks = (await db.execute(stmt)).scalars().all()
            full_text = "\n".join([c.chunk_text for c in chunks])

        return {
            "success": True,
            "data": {
                "id": document_id,
                "filename": doc.filename,
                "content": full_text,
                "summary": build_brief_summary(full_text),
                "suggested_tags": build_suggested_tags(full_text),
            }
        }
    except Exception as e:
        logger.error(f"Get document content failed: {str(e)}")
        raise HTTPException(status_code=500, detail="获取文档内容失败")

@router.get("/{document_id}/download", dependencies=[Depends(get_current_user)])
async def download_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        doc = await db.get(Document, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")

        if doc.organization_id != current_user.organization_id and doc.uploaded_by != current_user.id:
            raise HTTPException(status_code=403, detail="无权下载该文档")

        url = minio_client.get_presigned_url(doc.file_path)
        return RedirectResponse(url=url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download document failed: {str(e)}")
        raise HTTPException(status_code=500, detail="下载文档失败")
