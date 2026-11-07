"""Application settings, read from the environment or a local .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-secret-change-me-dev-only-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./nelp.db"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    public_base_url: str = "http://localhost:3000"  # the front end; used in SMS links
    seed_on_startup: bool = False

    # Secrets
    secret_key: str = DEV_SECRET  # signs JWTs and keys the one-time-code HMACs
    payment_webhook_secret: str = DEV_SECRET
    sms_dlr_secret: str = DEV_SECRET
    jwt_ttl_minutes: int = 720

    # SMS
    sms_provider: Literal["demo", "thrivevas"] = "demo"
    thrivevas_api_key: str | None = None
    thrivevas_sender_id: str | None = None
    # Seeded accounts use this prefix. They always go to the demo inbox, so a
    # live provider never texts a real stranger who owns a "demo" number.
    demo_phone_prefix: str = "+25470000"

    # Payments. Only the simulator exists until a sandbox is confirmed.
    payment_provider: Literal["simulated"] = "simulated"

    # Marketplace rules
    booking_duration_minutes: int = 60
    max_days_ahead: int = 30
    fare_base_kes: int = 100
    fare_per_km_kes: int = 30
    platform_fee_bps: int = 1000  # 10%
    code_ttl_hours: int = 24
    max_code_attempts: int = 5
    max_code_resends: int = 3

    # S3-compatible file storage (RumptyCloud). Uploads answer 503 until all are set.
    s3_endpoint_url: str = "https://s3.rumptycloud.com"
    s3_region: str = "olas-closet"
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    max_upload_bytes: int = 5 * 1024 * 1024
    profile_image_url_ttl_seconds: int = 3600
    document_url_ttl_seconds: int = 300

    # Optional plain-language score explanation. Falls back to a fixed template.
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"

    # Seed
    demo_pin: str = "1234"
    admin_phone: str | None = None
    admin_pin: str | None = None

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_production(self):
        if self.environment == "production":
            for name in ("secret_key", "payment_webhook_secret", "sms_dlr_secret"):
                if getattr(self, name) == DEV_SECRET:
                    raise ValueError(f"{name.upper()} must be set in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
