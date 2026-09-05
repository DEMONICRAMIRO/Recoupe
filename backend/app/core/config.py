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
    risk_max_calls_7d: int = 1
    renewal_ltv_call_threshold: float = 50000.0
    opencode_api_key: str | None = None
    opencode_base_url: str = "https://opencode.ai/zen/go"
    groq_api_key: str | None = None
    model_name: str = "deepseek-v4-flash"
    test_model_name: str = "deepseek-v4-flash"
    llm_enabled: bool = False
    llm_max_tokens: int = 2048
    twilio_voice_from: str | None = None
    twilio_voice_twiml_url: str | None = None
    # Invoice Agent gate — independent counters, not shared with Payment/Renewal/Cart
    invoice_max_escalations_30d: int = 2
    invoice_contact_cooldown_hours: int = 48
    # Razorpay (Phase 5)
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    # Frontend / CORS
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
