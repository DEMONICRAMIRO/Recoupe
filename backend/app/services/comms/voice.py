from urllib.parse import quote, urlencode

from twilio.rest import Client
from twilio.twiml.voice_response import Say, VoiceResponse

from app.core.config import settings
from app.services.comms import SendResult
from app.services.comms.sms import SUCCESS_STATUSES

ACCEPTED_CALL_STATUSES = {"queued", "ringing", "in-progress", "completed", "answered"}


def _voice_twiml(script: str) -> str:
    response = VoiceResponse()
    say = Say(script, voice="Polly.Aditi", language="en-IN")
    response.append(say)
    return str(response)


def _bin_url_with_params(url_params: dict | None) -> str:
    base = settings.twilio_voice_twiml_url or ""
    if not base:
        return ""
    if not url_params:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(url_params, quote_via=quote)}"


def place_voice_call(to: str, script: str, *, url_params: dict | None = None) -> SendResult:
    required = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
        "TWILIO_VOICE_FROM": settings.twilio_voice_from,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Twilio Voice configuration: {', '.join(missing)}")
    if not to:
        raise ValueError("Voice recipient is required")

    try:
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        bin_url = _bin_url_with_params(url_params)
        if bin_url:
            call = client.calls.create(to=to, from_=settings.twilio_voice_from, url=bin_url)
        else:
            call = client.calls.create(
                to=to,
                from_=settings.twilio_voice_from,
                twiml=_voice_twiml(script),
            )
        status = call.status or "unknown"
        return SendResult(
            success=status in ACCEPTED_CALL_STATUSES,
            provider="twilio_voice",
            provider_id=call.sid,
            status=status,
        )
    except Exception as exc:
        return SendResult(
            success=False,
            provider="twilio_voice",
            provider_id=None,
            status="failed",
            error=str(exc),
        )
