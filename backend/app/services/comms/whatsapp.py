from twilio.rest import Client

from app.core.config import settings
from app.services.comms import SendResult
from app.services.comms.sms import SUCCESS_STATUSES, send_sms


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
        return _fallback(to, body, f"Missing Twilio WhatsApp configuration: {', '.join(missing)}")
    if not to:
        raise ValueError("WhatsApp recipient is required")

    try:
        message = Client(settings.twilio_account_sid, settings.twilio_auth_token).messages.create(
            body=body,
            from_=_whatsapp_address(settings.twilio_whatsapp_from),
            to=_whatsapp_address(to),
        )
        status = message.status or "unknown"
        if status in SUCCESS_STATUSES:
            return SendResult(
                success=True,
                provider="twilio_whatsapp",
                provider_id=message.sid,
                status=status,
            )
        return _fallback(to, body, f"WhatsApp returned non-success status {status!r}")
    except Exception as exc:
        return _fallback(to, body, str(exc))


def _fallback(to: str, body: str, whatsapp_error: str) -> SendResult:
    sms_result = send_sms(to, body)
    return SendResult(
        success=sms_result.success,
        provider=sms_result.provider,
        provider_id=sms_result.provider_id,
        status=f"{sms_result.status}_whatsapp_fallback",
        error=f"WhatsApp unavailable ({whatsapp_error}); fell back to SMS",
    )
