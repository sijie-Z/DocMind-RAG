"""Infrastructure settings — database, Redis, Elasticsearch, Kafka, MinIO, file upload."""


from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database, Redis, Elasticsearch, Kafka, MinIO, and file upload settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # MySQL
    DATABASE_URL: str = ""
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL must be set in production")
        return v

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    # Elasticsearch
    ELASTICSEARCH_HOSTS: list[str] = ["http://localhost:9200"]
    ELASTICSEARCH_INDEX_NAME: str = "paicongming_knowledge"
    ES_ANALYZER: str = "standard"
    ES_SEARCH_ANALYZER: str = "standard"

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: list[str] | str = "localhost:9092"
    KAFKA_FILE_PROCESSING_TOPIC: str = "file-processing"
    KAFKA_CHAT_PROCESSING_TOPIC: str = "chat-processing"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET_NAME: str = "docmind"
    MINIO_SECURE: bool = False
    MINIO_REGION: str = "us-east-1"
    UPLOAD_TEMP_DIR: str = "temp_uploads"

    # File upload
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB
    CHUNK_SIZE: int = 5 * 1024 * 1024  # 5MB
    UPLOAD_DIR: str = "uploads"
    USER_STORAGE_LIMIT_BYTES: int = 10 * 1024 * 1024 * 1024

    # Document parsing
    OCR_LANGUAGE: str = "chi_sim+eng"
    SUPPORTED_FILE_TYPES: list[str] = [
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
        "txt", "md", "csv", "json", "xml", "html",
        "jpg", "jpeg", "png", "bmp", "tiff", "tif",
    ]
