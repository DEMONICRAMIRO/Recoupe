import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from app.core.config import settings
from app.services.comms import SendResult


def send_email(to: str, subject: str, body: str) -> SendResult:
    required = {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_USER": settings.smtp_user,
        "SMTP_PASS": settings.smtp_pass,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SMTP configuration: {', '.join(missing)}")
    if not to:
        raise ValueError("Email recipient is required")

    message = EmailMessage()
    message["From"] = settings.smtp_user
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_pass)
            refused = server.send_message(message)
        if refused:
            return SendResult(
                success=False,
                provider="smtp",
                provider_id=message["Message-ID"],
                status="rejected",
                error=f"SMTP refused recipients: {', '.join(refused)}",
            )
        return SendResult(
            success=True,
            provider="smtp",
            provider_id=message["Message-ID"],
            status="accepted",
        )
    except (OSError, smtplib.SMTPException) as exc:
        return SendResult(
            success=False,
            provider="smtp",
            provider_id=message["Message-ID"],
            status="failed",
            error=str(exc),
        )
