import logging
from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.core.config import settings

logger = logging.getLogger(__name__)


def normalize_object_name(file_path: str) -> str:
    """将存储的 file_path（对象名或完整 URL）统一解析为 MinIO 对象名。

    兼容历史数据两种存储格式：
    - 对象名：documents/{org}/{uuid}.pdf 或 {org}/{md5}.pdf
    - 完整 URL：http://{endpoint}/{bucket}/{object...}
    """
    if not file_path:
        return file_path
    if "://" in file_path:
        # http(s)://host/bucket/object/... → 取 bucket 之后的部分
        parts = file_path.split("/")
        try:
            bucket_index = parts.index(settings.MINIO_BUCKET_NAME)
            return "/".join(parts[bucket_index + 1:])
        except ValueError:
            # bucket 名不在路径中：退化为取最后一段之后的全部（兼容旧格式）
            return "/".join(parts[4:]) if len(parts) > 4 else parts[-1]
    return file_path


class MinioClient:
    def __init__(self):
        self._client = None
        # 修复：初始化桶检查标志，避免 _ensure_bucket_exists 启用即崩
        self._bucket_checked = False

    @property
    def client(self):
        if self._client is None:
            self._client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
                region=settings.MINIO_REGION,
            )
        return self._client

    @property
    def bucket_name(self):
        return settings.MINIO_BUCKET_NAME

    def _ensure_bucket_exists(self):
        if self._bucket_checked:
            return
        self._bucket_checked = True
        try:
            if not self._client.bucket_exists(self.bucket_name):
                self._client.make_bucket(self.bucket_name)
                logger.info(f"Created bucket {self.bucket_name}")
        except Exception as e:
            logger.warning(f"MinIO bucket check failed (non-blocking): {e}")

    def put_object(self, object_name: str, data: BinaryIO, length: int, content_type: str = "application/octet-stream", **kwargs):
        try:
            target_bucket = kwargs.get("bucket_name") or kwargs.get("bucket") or self.bucket_name
            self.client.put_object(target_bucket, object_name, data, length, content_type=content_type)
            logger.info(f"Uploaded {object_name} to {target_bucket}")
            return object_name
        except S3Error as e:
            logger.error(f"Error uploading object: {e}")
            raise

    def get_object(self, object_name: str):
        try:
            return self.client.get_object(self.bucket_name, object_name)
        except S3Error as e:
            logger.error(f"Error getting object: {e}")
            raise

    def stat_object(self, object_name: str, **kwargs):
        try:
            target_bucket = kwargs.get("bucket_name") or kwargs.get("bucket") or self.bucket_name
            return self.client.stat_object(target_bucket, object_name)
        except S3Error as e:
            logger.error(f"Error stat object: {e}")
            raise

    def get_presigned_url(self, object_name: str, expires: timedelta = timedelta(hours=1)):
        try:
            return self.client.get_presigned_url("GET", self.bucket_name, object_name, expires=expires)
        except S3Error as e:
            logger.error(f"Error getting presigned URL: {e}")
            raise

    def fget_object(self, bucket_name: str, object_name: str, file_path: str):
        try:
            return self.client.fget_object(bucket_name, object_name, file_path)
        except Exception as e:
            logger.error(f"Error downloading object to file: {e}")
            raise

    def remove_object(self, object_name: str, bucket_name: str = None):
        try:
            target_bucket = bucket_name or self.bucket_name
            self.client.remove_object(target_bucket, object_name)
            logger.info(f"Removed {object_name} from {target_bucket}")
        except S3Error as e:
            logger.error(f"Error removing object: {e}")
            raise


minio_client = MinioClient()
