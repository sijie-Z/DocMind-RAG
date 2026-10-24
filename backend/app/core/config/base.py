"""Core application settings — app metadata, logging, monitoring, rate limiting."""

from typing import Literal

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
    APP_VERSION: str = "1.22.1-rc.1"
    DEBUG: bool = False
    ENABLE_DEMO_ACCOUNT: bool = False
    ENABLE_ENSURE_DEMO_ENDPOINT: bool = False
    EXPOSE_EXCEPTION_DETAIL: bool = False
    HOST: str = "0.0.0.0"  # nosec B104 - 容器内需绑定全部网卡，可由环境变量覆盖
    PORT: int = 8000

    # ── 数据库迁移（issue #495）────────────────────────────────────────────
    # 启动期 schema guard：数据库 alembic_version 与迁移 head 不一致时拒绝启动
    # （exit 2）。迁移本身由 compose 一次性 migrate 服务 / K8s Job 串行执行，
    # 不依赖下面两个开关；它们只控制「启动期是否校验 / 是否兜底自动迁移」。
    SCHEMA_GUARD: bool = True
    # 兜底自动迁移：仅显式打开时才在启动期执行 alembic upgrade head（审计日志）。
    # 默认关闭——多个 backend/worker 并发启动时各自迁移会竞争，迁移必须串行。
    AUTO_MIGRATE: bool = False

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/docmind.log"
    LOG_JSON: bool = True
    # SQL 语句回显开关（issue #493）：与 DEBUG 解耦，默认关闭。SQLAlchemy 在
    # echo=True 时会往具名 logger ``sqlalchemy.engine.Engine`` 挂
    # ``StreamHandler(sys.stdout)``——stdio 协议进程的 stdout 只允许协议帧，
    # 因此不得再用 DEBUG 顺带打开 SQL 回显；需要排障时显式置 true。
    SQL_ECHO: bool = False

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
        # issue #223：探针端点必须免限流（k8s 每 10–15s 打一次，且不带业务身份）
        "/livez",
        "/readyz",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/static",
    ]

    # Workflow safety: code nodes run in-process unless explicitly enabled.
    ENABLE_WORKFLOW_CODE_NODES: bool = False

    # User Commands（issue #475）kill-switch：关闭后命令通道整体回退——
    # GET /commands 返回 enabled=false + 空清单（前端回退本地模板），
    # POST /commands/execute 404 COMMANDS_DISABLED，聊天入口不再拦截 "/" 前缀
    # （恢复到"斜杠文本按普通提示词处理"的旧行为）。
    COMMANDS_ENABLED: bool = True

    # ── SDK / JSON-RPC / SSE 续传（issue #480）────────────────────────────
    # ``POST /api/v1/rpc`` 开关（issue #493：默认开启）。仅构建了 FastAPI app
    # 的入口（web 等）才会挂载该路由；sdk profile 只跑 stdio、不构建 HTTP app，
    # 因此不受本开关影响。关闭时端点如实 404。
    RPC_HTTP_ENABLED: bool = True
    # HTTP 批量请求上限（issue §5.3：批量 ≤ 20）。
    RPC_BATCH_MAX: int = 20
    # stdio NDJSON 单行上限（1MB）。
    RPC_LINE_MAX_BYTES: int = 1_048_576
    # 服务端→客户端请求（session/request_permission）等待上限（秒）。
    RPC_PERMISSION_TIMEOUT_SECONDS: int = 300

    # ── ACP v1（issue #481）────────────────────────────────────────────────
    # ``session/request_permission``（审批桥）等待上限（秒）；超时按拒绝（deny）
    # 收尾，任务不得悬空。与 RPC 面同默认值、独立旋钮（两种入口可分别调）。
    ACP_PERMISSION_TIMEOUT_SECONDS: int = 300
    # ACP 不支持交互式计划审批：收到 plan_approval_required 时，
    #   reject（默认）— 计划审批按拒绝收尾并回 JSON-RPC -32009；
    #   auto          — 记录审计日志后自动批准并续跑（计划照常执行）。
    ACP_PLAN_APPROVAL_MODE: Literal["reject", "auto"] = "reject"

    # SSE 断连保留：true 时不立即取消 pipeline，交给 SSE_ORPHAN_TTL_SECONDS 兜底。
    SSE_KEEPALIVE_ON_DISCONNECT: bool = True
    # 孤儿 pipeline 最长保留（秒）；到期取消，绝不无限保留。
    SSE_ORPHAN_TTL_SECONDS: int = 600
    # 每会话 Redis ring buffer 条数上限 / TTL。
    SSE_BUFFER_MAXLEN: int = 1000
    SSE_BUFFER_TTL_SECONDS: int = 3600

    # API Key 默认 scope（新建 key 未显式指定时使用）。
    API_KEY_DEFAULT_SCOPES: list[str] = [
        "chat:read",
        "chat:write",
        "knowledge:read",
        "rpc",
    ]

    # Hooks（issue #474）默认关闭：false 时**不读取** HOOKS_CONFIG_PATH、不注册
    # 任何外部 hook，agent loop / executor 行为与基线逐字节一致（回滚开关）。
    # true 时启动期加载并严格校验；非法配置 fail-closed（进程退出）。
    HOOKS_ENABLED: bool = False
    HOOKS_CONFIG_PATH: str = "config/hooks.yaml"

    # Webhook runtime（issue #478）——默认全关。开启接收/outbox poll 需要
    # WEBHOOKS_ENABLED=true，且必须同时配置 SecuritySettings.WEBHOOK_ENCRYPTION_KEY
    # （独立 Fernet key；缺失/非法则启动失败，见 config loader 的 fail-closed 校验）。
    WEBHOOKS_ENABLED: bool = False
    # 签名时间戳容差（秒）：|now - webhook-timestamp| 超过即 401（防重放）。
    WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS: int = 300
    # 全局 body 上限（规则 max_body_bytes 只能收紧到不高于此值）。
    WEBHOOK_MAX_BODY_BYTES: int = 1048576
    # 每规则默认每分钟限流（规则可单独下调）。未知 rule_id 的请求也按此值
    # 以 ``unknown:{ip}`` 桶计数（issue #497：限流先于验签，坏签名同样消耗配额）。
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 60
    # outbox poll 间隔（秒）。
    WEBHOOK_WORKER_POLL_SECONDS: int = 2
    # 有界重试次数；达到后 failed + next_attempt_at=NULL（终态，可被同 id 重投重置）。
    WEBHOOK_MAX_ATTEMPTS: int = 3
    # session_mode=reuse 命中后加载的历史消息条数上限（issue #497）：最近 N 条，
    # 按 agent 聊天格式传入 AgentService.chat(history=...)。
    WEBHOOK_REUSE_HISTORY_LIMIT: int = 20
