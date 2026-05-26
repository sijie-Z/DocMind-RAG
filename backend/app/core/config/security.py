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
