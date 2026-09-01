import json

from twilio.rest import Client

from app.core.config import settings
from app.services.comms import SendResult


SUCCESS_STATUSES = {"accepted", "queued", "sending", "sent", "delivered"}


def send_sms(
    to: str,
    body: str,
    *,
    content_sid: str | None = None,
    content_variables: dict[str, str] | None = None,
) -> SendResult:
    required = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
        "TWILIO_SMS_FROM": settings.twilio_sms_from,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Twilio SMS configuration: {', '.join(missing)}")
    if not to:
        raise ValueError("SMS recipient is required")

    try:
        message_args = {"from_": settings.twilio_sms_from, "to": to}
        active_content_sid = content_sid or settings.twilio_sms_content_sid
        if active_content_sid:
            message_args["content_sid"] = active_content_sid
            message_args["content_variables"] = json.dumps(content_variables or {})
        else:
            message_args["body"] = body
        message = Client(settings.twilio_account_sid, settings.twilio_auth_token).messages.create(**message_args)
        status = message.status or "unknown"
        return SendResult(
            success=status in SUCCESS_STATUSES,
            provider="twilio_sms",
            provider_id=message.sid,
            status=status,
        )
    except Exception as exc:
        return SendResult(
            success=False,
            provider="twilio_sms",
            provider_id=None,
            status="failed",
            error=str(exc),
        )
