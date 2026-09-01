from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIELDOPS_", env_file=".env", extra="ignore")

    app_name: str = "FieldOps Cloud API"
    database_url: str = "postgresql+asyncpg://fieldops:fieldops@localhost:5432/fieldops"
    jwt_secret: SecretStr = SecretStr("local-development-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    cors_origins: str = "http://localhost:8060"
    admin_email: str = "admin@example.com"
    admin_password: SecretStr = SecretStr("ChangeMe123!")
    dispatcher_email: str = "dispatcher@example.com"
    dispatcher_password: SecretStr = SecretStr("ChangeMe123!")
    technician_email: str = "technician@example.com"
    technician_password: SecretStr = SecretStr("ChangeMe123!")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
