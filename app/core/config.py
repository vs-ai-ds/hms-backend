# app/core/config.py
import json
from functools import lru_cache

from pydantic import EmailStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"

    # Security
    secret_key: str = "changeme"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Database
    database_url: str

    # Redis
    redis_url: str | None = None

    # Email
    email_backend: str = "smtp"
    email_from: EmailStr = "no-reply@hms.com"
    email_from_name: str | None = None  # Display name for email sender
    email_smtp_host: str = "localhost"
    email_smtp_port: int = 1025
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_use_tls: bool = True
    email_sandbox_mode: bool = True
    email_test_recipient: EmailStr | None = None
    resend_api_key: str | None = None

    # SMS / WhatsApp flags
    sms_enabled: bool = False
    whatsapp_enabled: bool = False
    sms_provider: str = "none"
    whatsapp_provider: str = "none"

    # Patient notification flags
    send_email_to_patients: bool = False  # Set to True to send emails to patients
    send_sms_to_patients: bool = False  # Set to True to send SMS to patients (requires SMS_ENABLED=True and SMS provider configured)

    # SMS provider configuration (for Twilio or other providers)
    sms_provider_account_sid: str | None = None  # Twilio Account SID
    sms_provider_auth_token: str | None = None  # Twilio Auth Token
    sms_from_number: str | None = None  # Phone number to send SMS from

    # Tenant schema management
    # If true, allows dropping and recreating tenant schema objects during registration (dev only)
    # Default is false (production-safe behavior)
    hms_dev_allow_tenant_schema_reset: bool = False

    # Max attempts to generate a unique schema name in rare collision cases
    # Note: .env can use HMS_SCHEMA_NAME_MAX_ATTEMPTS or HMS_TENANT_SCHEMA_NAME_MAX_ATTEMPTS
    hms_tenant_schema_name_max_attempts: int = 10
    hms_schema_name_max_attempts: int | None = None  # Alias for backward compatibility

    # OPD Appointment lifecycle configuration
    opd_no_show_minutes_after_scheduled: int = 180  # 3 hours default
    opd_checkin_grace_minutes: int = 30  # 30 minutes grace period for check-in
    opd_rx_create_window_hours_past: int = (
        2  # Can create Rx for appointments up to 2 hours in past
    )
    opd_rx_create_window_hours_future: int = (
        24  # Can create Rx for appointments up to 24 hours in future
    )

    # Date format configuration
    # Options: "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "DD-MM-YYYY"
    date_format: str = "DD/MM/YYYY"

    # File storage
    file_storage_root: str = "uploads"

    # Demo mode
    demo_mode: bool = False  # Enable demo mode features (demo refresh endpoint, etc.)
    demo_auto_refresh_on_login: bool = (
        False  # Auto-freshen demo data on login (DEMO only)
    )
    demo_refresh_ttl_hours: int = (
        24  # TTL in hours for auto-refresh check (default 24h)
    )
    demo_freshen_days: int = 7  # Default days to shift forward for freshen (default 7)

    # Web Push (VAPID)
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_sub: str | None = None  # mailto: email for VAPID subscription

    # Super Admin (for setup_platform.py script)
    super_admin_email: str | None = None
    super_admin_password: str | None = None
    super_admin_first_name: str | None = None
    super_admin_last_name: str | None = None

    backend_cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from JSON array or comma-separated string."""
        if isinstance(v, str):
            # Try parsing as JSON first
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            # If not JSON, try comma-separated values
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance so the .env is parsed once.
    Validates required environment variables and provides meaningful errors.
    """
    try:
        settings = Settings()
    except Exception as e:
        raise ValueError(
            f"Failed to load settings. Please check your .env file. Error: {e}"
        ) from e

    # Handle schema name max attempts alias (backward compatibility)
    if settings.hms_schema_name_max_attempts is not None:
        settings.hms_tenant_schema_name_max_attempts = (
            settings.hms_schema_name_max_attempts
        )

    # Validate critical settings
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required but not set in .env file")

    if settings.secret_key == "changeme":
        import warnings

        warnings.warn(
            "SECRET_KEY is set to default 'changeme'. Please change it in production!",
            UserWarning,
        )

    # Validate email settings based on backend
    if settings.email_backend == "resend" and not settings.resend_api_key:
        raise ValueError(
            "RESEND_API_KEY is required when EMAIL_BACKEND=resend but not set in .env file"
        )

    if settings.email_backend == "smtp":
        if not settings.email_smtp_host:
            raise ValueError(
                "EMAIL_SMTP_HOST is required when EMAIL_BACKEND=smtp but not set in .env file"
            )

    return settings
