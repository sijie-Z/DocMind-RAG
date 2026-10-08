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
    REDIS_PORT: int = 6390
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    # Elasticsearch
    # 注：分词器不是配置项——运行时由 _ESHolder 自动选择（默认 cjk，探测到 IK 后
    # 用 ik_smart/ik_max_word，见 core/elasticsearch.py）。此前的 ES_ANALYZER /
    # ES_SEARCH_ANALYZER 字段零消费且默认值 "standard" 与运行时矛盾
    #（issue #242：误导性强于无用，已删除）。
    ELASTICSEARCH_HOSTS: list[str] = ["http://localhost:9200"]
    ELASTICSEARCH_INDEX_NAME: str = "paicongming_knowledge"

    # Kafka
    # 注：chat 链路从不走 Kafka（消费者只订阅 file-processing），此前的
    # KAFKA_CHAT_PROCESSING_TOPIC 字段零消费（issue #242 已删除）。
    KAFKA_BOOTSTRAP_SERVERS: list[str] | str = "localhost:9092"
    KAFKA_FILE_PROCESSING_TOPIC: str = "file-processing"

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
    # 注：支持的文件类型不是配置项——白名单与解析器能力强耦合（file_service 的
    # allowed_extensions / document_parser 的 supported_extensions / 前端 accept
    # 三处同为 7 种格式）。此前的 SUPPORTED_FILE_TYPES 字段零消费（issue #242 已删除）；
    # 未来若要放开更多格式，正确动作是扩展 parser，而不是把常量改读 env。
    OCR_LANGUAGE: str = "chi_sim+eng"
