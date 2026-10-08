"""Security settings — JWT, CORS, secret keys."""


from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SecuritySettings(BaseSettings):
    """Authentication, authorization, and CORS settings.

    SECRET_KEY and JWT_SECRET_KEY must be set in .env — empty defaults
    cause a startup-time validation error to prevent accidental insecure deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # JWT
    # 保留字段（issue #242 终单标注）：除下方 validator 的非空启动校验外，
    # SECRET_KEY 当前无任何代码消费（全仓无加密/hmac/Fernet 读取点）。保留原因是
    # 真实部署契约：k8s（02-secrets.yaml）、.env 样例、CI 均注入它，validator 借
    # 此拦住「无密钥启动」。未来若赋予用途，优先候选=provider API key 的静态加密
    #（user/llm_config 的 api_key 目前明文落盘）。
    SECRET_KEY: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
    ]

    @model_validator(mode="after")
    def _check_secrets(self) -> "SecuritySettings":
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set in .env "
                '(generate with: python -c "import secrets; print(secrets.token_hex(32))")'
            )
        if not self.JWT_SECRET_KEY:
            raise ValueError(
                "JWT_SECRET_KEY must be set in .env "
                '(generate with: python -c "import secrets; print(secrets.token_hex(32))")'
            )
        return self
