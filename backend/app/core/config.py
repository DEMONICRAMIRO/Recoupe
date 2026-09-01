from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/revenue_recovery"
    app_name: str = "Revenue Recovery Agent"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None
    twilio_sms_from: str | None = None
    twilio_sms_content_sid: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    payment_link_base_url: str = "https://rzp.io/i/recovery-demo"
    risk_max_retries: int = 3
    risk_cooldown_hours: int = 24

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
