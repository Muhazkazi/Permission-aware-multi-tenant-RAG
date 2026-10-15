"""Environment-based application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
MINIMUM_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional .env file."""

    app_name: str = "Permission-Aware Multi-Tenant RAG API"
    app_env: str = "development"
    app_version: str = "0.1.0"
    database_url: str | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=15, ge=1, le=60)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_jwt_settings(self) -> "Settings":
        """Require a usable signing configuration when running in production."""

        if self.app_env.lower() in {"production", "prod"}:
            self.require_jwt_secret()
        return self

    def require_jwt_secret(self) -> str:
        """Return a validated JWT secret for security-sensitive operations."""

        secret = self.jwt_secret_key.get_secret_value() if self.jwt_secret_key else ""
        if len(secret) < MINIMUM_JWT_SECRET_LENGTH:
            message = "JWT_SECRET_KEY must be at least 32 characters long."
            raise ValueError(message)
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            message = "JWT_ALGORITHM must be one of the supported HMAC algorithms."
            raise ValueError(message)
        return secret


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the running application."""

    return Settings()
