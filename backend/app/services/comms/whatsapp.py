from twilio.rest import Client

from app.core.config import settings
from app.services.comms import SendResult
from app.services.comms.sms import SUCCESS_STATUSES


def _whatsapp_address(value: str) -> str:
    return value if value.startswith("whatsapp:") else f"whatsapp:{value}"


def send_whatsapp(to: str, body: str) -> SendResult:
    required = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
        "TWILIO_WHATSAPP_FROM": settings.twilio_whatsapp_from,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Twilio WhatsApp configuration: {', '.join(missing)}")
    if not to:
        raise ValueError("WhatsApp recipient is required")

    try:
        message = Client(settings.twilio_account_sid, settings.twilio_auth_token).messages.create(
            body=body,
            from_=_whatsapp_address(settings.twilio_whatsapp_from),
            to=_whatsapp_address(to),
        )
        status = message.status or "unknown"
        return SendResult(
            success=status in SUCCESS_STATUSES,
            provider="twilio_whatsapp",
            provider_id=message.sid,
            status=status,
        )
    except Exception as exc:
        return SendResult(
            success=False,
            provider="twilio_whatsapp",
            provider_id=None,
            status="failed",
            error=str(exc),
        )
