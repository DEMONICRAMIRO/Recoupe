"""audit.py — Audit log read endpoints with filter + customer-scoped queries.

Supports ?status=recovered|blocked|pending|sent and ?customer_id= scoping.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow
from app.db.session import SessionLocal

router = APIRouter(prefix="/audit", tags=["audit"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Status → action_taken mapping (matches the mockup's 4 filter states)
RECOVERED_ACTIONS = {
    "retry_now", "retry_alt_route", "send_payment_link",
    "send_reminder", "send_firm_followup",
    "escalate_with_call", "escalate_no_call",
    "nudge", "nudge_5%", "nudge_10%",
    "plain", "discount",
    "prompt_card_update", "grace_period", "silent_retry",
}
BLOCKED_ACTIONS = {"hold", "voice_call_blocked"}
# "sent" = comms dispatched but recovery not yet confirmed
# (In our pipeline these are the same actions but for non-payment events)
SENT_ACTIONS = {
    "send_reminder", "send_firm_followup", "escalate_no_call",
    "nudge", "nudge_5%", "nudge_10%", "plain", "discount",
    "prompt_card_update", "grace_period", "silent_retry",
}

EVENT_TYPE_LABEL = {
    "payment_failed": "Payment failed",
    "cart_abandoned": "Cart abandoned",
    "renewal_failed": "Renewal failed",
    "invoice_overdue": "Invoice overdue",
}


def _serialize_row(row: AuditLogRow, event: EventRow | None) -> dict:
    """Serialize a single audit row + joined event into the dashboard row shape."""
    if event:
        event_label = EVENT_TYPE_LABEL.get(event.event_type, event.event_type)
        amount = f"₹{event.amount:,.2f}"
        customer_id = event.customer_id
        event_type = event.event_type
    else:
        event_label = "unknown"
        amount = "₹0.00"
        customer_id = row.event_id
        event_type = ""

    action = row.action_taken
    if action in BLOCKED_ACTIONS:
        status = "blocked"
        status_label = "Blocked"
    elif action in SENT_ACTIONS and event_type not in ("payment_failed",):
        status = "sent"
        status_label = "Sent"
    elif action in RECOVERED_ACTIONS:
        status = "recovered"
        status_label = "Recovered"
    else:
        status = "pending"
        status_label = "Pending"

    # Pipeline trail: extract from reasoning or stage_trace if stored
    trail = [1, 1, 1, 1, 1, 1] if status not in ("pending",) else [1, 1, 0, 0, 0, 0]
    if status == "blocked":
        trail = [1, 1, 1, -1, 0, 0]

    return {
        "id": row.id,
        "event_id": row.event_id,
        "customer_id": customer_id,
        "event_type": event_type,
        "event_label": event_label,
        "action_taken": action,
        "reasoning": row.reasoning,
        "amount": amount,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "status": status,
        "status_label": status_label,
        "trail": trail,
    }


@router.get("/entries")
def list_audit_entries(
    status: str | None = Query(None, description="recovered|blocked|pending|sent|all"),
    customer_id: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    """Return audit log entries with optional status filter and customer scoping."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    query = select(AuditLogRow).where(AuditLogRow.timestamp >= since)
    if customer_id:
        # Join through events to filter by customer
        event_ids = db.execute(
            select(EventRow.event_id)
            .where(EventRow.customer_id == customer_id)
        ).scalars().all()
        query = query.where(AuditLogRow.event_id.in_(event_ids))

    rows = db.execute(
        query.order_by(AuditLogRow.timestamp.desc()).offset(offset).limit(limit)
    ).scalars().all()

    # Batch-fetch associated events
    event_ids = [r.event_id for r in rows]
    events_by_id: dict[str, EventRow] = {}
    if event_ids:
        event_rows = db.execute(
            select(EventRow).where(EventRow.event_id.in_(event_ids))
        ).scalars().all()
        events_by_id = {e.event_id: e for e in event_rows}

    serialized = [_serialize_row(r, events_by_id.get(r.event_id)) for r in rows]

    # Apply status filter after serialization (simpler than SQL on action_taken set)
    if status and status != "all":
        serialized = [r for r in serialized if r["status"] == status]

    return {
        "total": len(serialized),
        "offset": offset,
        "limit": limit,
        "entries": serialized,
    }
