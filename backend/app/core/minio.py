"""
MinIO对象存储客户端
"""
from minio import Minio
from minio.error import S3Error
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_minio_client() -> Minio:
    """
    获取MinIO客户端实例
    
    Returns:
        MinIO客户端实例
    """
    try:
        client = Minio(
            endpoint=settings.MINIO_ENDPOINT.replace("http://", "").replace("https://", ""),
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
            region=settings.MINIO_REGION
        )
        
        # 检查存储桶是否存在，如果不存在则创建
        try:
            if not client.bucket_exists(settings.MINIO_BUCKET_NAME):
                client.make_bucket(settings.MINIO_BUCKET_NAME)
                logger.info(f"创建MinIO存储桶: {settings.MINIO_BUCKET_NAME}")
        except Exception as e:
            logger.warning(f"MinIO连接失败或存储桶检查失败: {e}。文件上传功能将不可用。")
        
        return client
        
    except Exception as e:
        logger.error(f"MinIO客户端初始化失败: {str(e)}")
        raise
    