from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRow, utc_now
from app.services.comms import SendResult
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.cart_feedback import CART_FEEDBACK_SMS

FEEDBACK_OPTIONS = {
    "1": "Too expensive",
    "2": "Payment issue",
    "3": "Just browsing",
    "4": "Other",
}


@dataclass(frozen=True)
class FeedbackChoice:
    raw: str
    canonical: str


def parse_feedback_reply(raw_reply: str) -> FeedbackChoice:
    cleaned = (raw_reply or "").strip().lower()
    for number, label in FEEDBACK_OPTIONS.items():
        if cleaned in {number, label.lower()}:
            return FeedbackChoice(raw=cleaned, canonical=label)
    return FeedbackChoice(raw=cleaned, canonical="Other")


def send_feedback_prompt(to: str, customer_name: str) -> SendResult:
    rendered = render(CART_FEEDBACK_SMS, customer_name=customer_name)
    return send_sms(to, rendered.body)


def record_feedback(db: Session, event_id: str, reason: str) -> bool:
    row = db.execute(
        select(AuditLogRow)
        .where(AuditLogRow.event_id == event_id)
        .order_by(AuditLogRow.id.desc())
    ).scalars().first()
    if row is None:
        return False
    suffix = f"; feedback_reason={reason}"
    if "feedback_reason=" not in (row.reasoning or ""):
        row.reasoning = f"{row.reasoning}{suffix}"
    else:
        row.reasoning = f"{row.reasoning.split('; feedback_reason=')[0]}{suffix}"
    row.timestamp = utc_now()
    db.commit()
    return True