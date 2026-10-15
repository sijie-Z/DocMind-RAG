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
    APP_VERSION: str = "1.21.0"
    DEBUG: bool = False
    ENABLE_DEMO_ACCOUNT: bool = False
    ENABLE_ENSURE_DEMO_ENDPOINT: bool = False
    EXPOSE_EXCEPTION_DETAIL: bool = False
    HOST: str = "0.0.0.0"  # nosec B104 - 容器内需绑定全部网卡，可由环境变量覆盖
    PORT: int = 8000

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/docmind.log"
    LOG_JSON: bool = True

    # Request ID —— 头名不是配置项（issue #242 已删除原 REQUEST_ID_HEADER 字段）：
    # X-Request-ID 是「前端 request.ts ↔ CORS 白名单 ↔ RequestIDMiddleware 默认参」
    # 三处硬编码的跨栈契约，单侧 env 旋钮没有可独立生效的空间。

    # Monitoring —— ENABLE_MONITORING 只控制「后台指标快照任务」（main.py lifespan）；
    # /metrics 端点与 PROMETHEUS_ENABLED 是独立开关，不受此值影响（issue #242 接线）。
    ENABLE_MONITORING: bool = True
    MONITORING_INTERVAL: int = 60
    SLOW_REQUEST_THRESHOLD_MS: int = 1200
    METRICS_DURATION_SAMPLE_SIZE: int = 2000
    METRICS_ROUTE_SAMPLE_SIZE: int = 300
    METRICS_LIVE_PERSIST_SECONDS: int = 5

    # Metrics endpoint protection: when non-empty, /metrics requires
    # "Authorization: Bearer <METRICS_TOKEN>". Leave empty to keep it open.
    METRICS_TOKEN: str = ""

    # Scheduler —— ENABLE_SCHEDULER 只控制「进程内定时任务调度器」（main.py lifespan
    # 启动的 asyncio 循环）；CRUD/run-now 接口不受此值影响。默认 true（与
    # ENABLE_MONITORING 同模式），测试环境 conftest 置 false。
    # 单实例语义：调度是进程内循环、无分布式锁——多 worker 部署需外部调度，
    # 属已知边界（见 app/models/scheduled_task.py 模块 docstring）。
    ENABLE_SCHEDULER: bool = True
    # 调度循环的轮询间隔（秒）。分钟级精度即够（cron 最细粒度为分钟），
    # 30s 保证到点后最迟半分钟触发。
    SCHEDULER_POLL_SECONDS: int = 30
    # 创建定时任务未显式给 timezone 时的默认 IANA 时区名（只影响 cron 解释）。
    SCHEDULER_DEFAULT_TIMEZONE: str = "UTC"

    # Prometheus 指标总开关（issue #207）：31 个指标默认 no-op。此前只有**真实
    # 环境变量**能打开它（prometheus.py 用 os.environ 判定），.env 配置无效——
    # 与 #232 同源的静默覆盖问题。现由 settings 承接；部署侧在
    # compose / k8s configmap / .env.docker 里置 true 即可。
    PROMETHEUS_ENABLED: bool = False

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

    # Workflow safety: code nodes run in-process unless explicitly enabled.
    ENABLE_WORKFLOW_CODE_NODES: bool = False
