"""Core application settings — app metadata, logging, monitoring, rate limiting."""


from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """Application-level settings: identity, logging, monitoring, rate limiting."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # App identity
    APP_NAME: str = "DocMind 企业级 RAG 知识库"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENABLE_DEMO_ACCOUNT: bool = False
    ENABLE_ENSURE_DEMO_ENDPOINT: bool = False
    EXPOSE_EXCEPTION_DETAIL: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/docmind.log"
    LOG_JSON: bool = True
    REQUEST_ID_HEADER: str = "X-Request-ID"

    # Monitoring
    ENABLE_MONITORING: bool = True
    MONITORING_INTERVAL: int = 60
    SLOW_REQUEST_THRESHOLD_MS: int = 1200
    METRICS_DURATION_SAMPLE_SIZE: int = 2000
    METRICS_ROUTE_SAMPLE_SIZE: int = 300

    # Alerting thresholds
    ALERT_ERROR_RATE_PERCENT: float = 5.0
    ALERT_P95_MS: float = 1200.0
    ALERT_ACTIVE_CONNECTIONS: int = 200
    ALERT_WEBHOOK_TOKEN: str = "dev-alert-token"
    ALERT_WEBHOOK_DEDUP_TTL_SECONDS: int = 300
    ALERT_RECENT_BUFFER_SIZE: int = 200

    # Rate limiting (fixed window)
    ENABLE_RATE_LIMIT: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 10000
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_EXCLUDE_PATHS: list[str] = [
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/static",
    ]
