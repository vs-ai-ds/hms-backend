from functools import lru_cache

from pydantic import EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"

    # Security
    secret_key: str = "changeme"  # override in .env
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Database
    database_url: str

    # Redis
    redis_url: str | None = None

    # Email
    email_from: EmailStr = "no-reply@example.com"
    email_smtp_host: str = "localhost"
    email_smtp_port: int = 1025
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_sandbox_mode: bool = True
    email_test_recipient: EmailStr | None = None

    # SMS / WhatsApp flags
    sms_enabled: bool = False
    whatsapp_enabled: bool = False
    sms_provider: str = "none"
    whatsapp_provider: str = "none"

    # File storage
    file_storage_root: str = "uploads"

    # Pydantic v2 style config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance so the .env is parsed once.
    """
    return Settings()