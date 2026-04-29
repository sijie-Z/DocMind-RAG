import logging
from typing import BinaryIO
from datetime import timedelta
from minio import Minio
from minio.error import S3Error
from app.core.config import settings

logger = logging.getLogger(__name__)


class MinioClient:
    def __init__(self):
        self._client = None
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
            self._ensure_bucket_exists()
        return self._client

    @property
    def bucket_name(self):
        return settings.MINIO_BUCKET_NAME

    def _ensure_bucket_exists(self):
        if self._bucket_checked:
            return
        try:
            if not self._client.bucket_exists(self.bucket_name):
                self._client.make_bucket(self.bucket_name)
                logger.info(f"Created bucket {self.bucket_name}")
            self._bucket_checked = True
        except Exception as e:
            logger.warning(f"Error ensuring bucket exists: {e}")

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


minio_client = MinioClient()
