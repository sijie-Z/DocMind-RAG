# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 核心配置
"""

from typing import List, Optional, Union
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )
    
    # 基础配置
    APP_NAME: str = "DocMind 企业级 RAG 知识库"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENABLE_DEMO_ACCOUNT: bool = False
    ENABLE_ENSURE_DEMO_ENDPOINT: bool = False
    EXPOSE_EXCEPTION_DETAIL: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # 安全配置
    SECRET_KEY: str = "your-secret-key-here"
    JWT_SECRET_KEY: str = "your-jwt-secret-key-here"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7天
    
    # CORS配置
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173"
    ]
    
    # --- 数据库配置 (关键修改) ---
    DATABASE_URL: str = "mysql+aiomysql://root:root@localhost:3306/paicongming_db"
    DATABASE_POOL_SIZE: int = 100
    DATABASE_MAX_OVERFLOW: int = 30
    
    # --- Redis配置 (关键修改) ---
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    
    # Elasticsearch配置 (关键修改) ---
    ELASTICSEARCH_HOSTS: List[str] = ["http://localhost:9200"]
    ELASTICSEARCH_INDEX_NAME: str = "paicongming_knowledge"
    ES_ANALYZER: str = "standard"  # 默认分词器，将在 init_elasticsearch 中动态检测
    ES_SEARCH_ANALYZER: str = "standard" # 默认搜索分词器
    
    # --- Kafka配置 (关键修改) ---
    KAFKA_BOOTSTRAP_SERVERS: Union[List[str], str] = "localhost:9092"
    KAFKA_FILE_PROCESSING_TOPIC: str = "file-processing"
    KAFKA_CHAT_PROCESSING_TOPIC: str = "chat-processing"
    
    # --- MinIO配置 (关键修改) ---
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "paicongming"
    MINIO_SECURE: bool = False
    MINIO_REGION: str = "us-east-1"
    UPLOAD_TEMP_DIR: str = "temp_uploads"
    
    # AI模型配置 (默认使用智谱AI全家桶)
    DEEPSEEK_API_KEY: str = "YOUR_API_KEY"
    DEEPSEEK_API_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    DEEPSEEK_MODEL: str = "glm-4-flash"
    AI_MAX_TOKENS: int = 10000  # LLM输出最大token数
    AI_STREAM_TIMEOUT: int = 120  # 流式输出超时(秒)
    
    # 本地模型配置 (Ollama/LocalAI) - 默认关闭
    ENABLE_LOCAL_LLM: bool = False
    LOCAL_LLM_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "llama3"
    
    # 本地向量模型配置 - 默认关闭
    ENABLE_LOCAL_EMBEDDING: bool = False
    LOCAL_EMBEDDING_MODEL: str = "nomic-embed-text"
    
    # Embedding配置 (默认使用智谱 embedding-3)
    EMBEDDING_API_KEY: Optional[str] = "YOUR_API_KEY"
    EMBEDDING_API_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    EMBEDDING_BASE_URL: Optional[str] = None
    EMBEDDING_MODEL: str = "embedding-3"
    
    # 文件上传配置
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB
    CHUNK_SIZE: int = 5 * 1024 * 1024  # 5MB
    UPLOAD_DIR: str = "uploads"
    USER_STORAGE_LIMIT_BYTES: int = 10 * 1024 * 1024 * 1024
    
    # 文档解析配置
    OCR_LANGUAGE: str = "chi_sim+eng"
    SUPPORTED_FILE_TYPES: List[str] = [
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
        "txt", "md", "csv", "json", "xml", "html",
        "jpg", "jpeg", "png", "bmp", "tiff", "tif"
    ]
    
    # 向量配置 (适配智谱 embedding-3)
    VECTOR_DIMENSION: int = 2048
    SIMILARITY_THRESHOLD: float = 0.7
    TOP_K_RESULTS: int = 10

    # RAG 检索策略开关（第二波优化）
    RAG_ENABLE_MMR: bool = True
    RAG_MMR_LAMBDA: float = 0.65
    RAG_MMR_CANDIDATE_MULTIPLIER: int = 4

    # Query Rewrite（多查询扩展）
    RAG_ENABLE_QUERY_REWRITE: bool = True
    RAG_QUERY_REWRITE_COUNT: int = 4

    # Reranker 配置 (默认使用智谱 rerank)
    RAG_ENABLE_RERANKER: bool = True
    RAG_RERANK_TOP_N: int = 20
    RAG_RERANK_TIMEOUT_SECONDS: float = 8.0
    
    # 兼容 .env 中的变量名
    RERANK_API_KEY: Optional[str] = "YOUR_API_KEY"
    RERANK_API_URL: Optional[str] = "https://open.bigmodel.cn/api/paas/v4/"
    RERANK_MODEL: str = "rerank"
    
    # 保留旧变量名用于兼容，但优先使用上面的
    RAG_RERANK_MODEL: Optional[str] = None
    RAG_RERANK_API_KEY: Optional[str] = None
    RAG_RERANK_API_URL: Optional[str] = None

    # 检索缓存 + 失败重试（第二波第4项）
    RAG_ENABLE_CACHE: bool = True
    RAG_CACHE_TTL_SECONDS: int = 600
    RAG_CACHE_MAX_SIZE: int = 1000
    RAG_RETRIEVAL_MAX_RETRIES: int = 2

    # 隐私与安全
    ENABLE_PII_MASKING: bool = True  # 是否开启敏感信息自动脱敏

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/paicongming.log"
    LOG_JSON: bool = False
    REQUEST_ID_HEADER: str = "X-Request-ID"
    
    # 监控配置
    ENABLE_MONITORING: bool = True
    MONITORING_INTERVAL: int = 60  # 秒
    SLOW_REQUEST_THRESHOLD_MS: int = 1200
    METRICS_DURATION_SAMPLE_SIZE: int = 2000
    METRICS_ROUTE_SAMPLE_SIZE: int = 300

    # 告警阈值（用于 monitoring/alerts）
    ALERT_ERROR_RATE_PERCENT: float = 5.0
    ALERT_P95_MS: float = 1200.0
    ALERT_ACTIVE_CONNECTIONS: int = 200
    ALERT_WEBHOOK_TOKEN: str = "dev-alert-token"
    ALERT_WEBHOOK_DEDUP_TTL_SECONDS: int = 300
    ALERT_RECENT_BUFFER_SIZE: int = 200

    # 限流配置（固定窗口）
    ENABLE_RATE_LIMIT: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 10000
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_EXCLUDE_PATHS: List[str] = [
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/static",
        "/api/v1/auth",
    ]
    
# 创建配置实例
settings = Settings()
