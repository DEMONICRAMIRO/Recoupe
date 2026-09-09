"""customers.py — Customer list and detail endpoints for the dashboard."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow
from app.db.session import SessionLocal

router = APIRouter(prefix="/customers", tags=["customers"])

RECOVERY_ACTIONS = {
    "retry_now", "retry_alt_route", "send_payment_link",
    "send_reminder", "send_firm_followup",
    "escalate_with_call", "escalate_no_call",
    "nudge", "nudge_5%", "nudge_10%",
    "plain", "discount",
    "prompt_card_update", "grace_period", "silent_retry",
}

EVENT_TYPE_LABEL = {
    "payment_failed": "Payment",
    "cart_abandoned": "Cart",
    "renewal_failed": "Renewal",
    "invoice_overdue": "Invoice",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _customer_initials(customer_id: str) -> str:
    parts = customer_id.replace("_", " ").replace("-", " ").split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return customer_id[:2].upper()


@router.get("/")
def list_customers(
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    """Return a list of customers with aggregated recovery stats.

    Driven by `events` — the table every ingest path (webhook, synthetic
    ingest, batch run) actually writes to — left-joined against
    `customer_history`, which is reference/profile data seeded
    separately and may not exist for a given customer.
    """
    customer_ids = db.execute(
        select(EventRow.customer_id).distinct().limit(limit)
    ).scalars().all()

    customers = []
    for cid in customer_ids:
        h = db.get(CustomerHistoryRow, cid)

        events = db.execute(
            select(EventRow).where(EventRow.customer_id == cid)
        ).scalars().all()

        event_ids = [e.event_id for e in events]
        event_types = list({e.event_type for e in events})
        event_type_labels = [EVENT_TYPE_LABEL.get(t, t) for t in event_types]

        # Get audit rows
        audit_rows = []
        if event_ids:
            audit_rows = db.execute(
                select(AuditLogRow).where(AuditLogRow.event_id.in_(event_ids))
            ).scalars().all()

        recovered_event_ids = {r.event_id for r in audit_rows if r.action_taken in RECOVERY_ACTIONS}
        recovered_amount = sum(
            e.amount for e in events if e.event_id in recovered_event_ids
        )
        contact_count_7d = h.contact_count_7d if h else 0

        customers.append({
            "customer_id": cid,
            "initials": _customer_initials(cid),
            "preferred_channel": h.preferred_channel if h else "sms",
            "reliability": _reliability_label(h.past_failures if h else None),
            "contact_count_7d": contact_count_7d,
            "past_failures": h.past_failures if h else 0,
            "event_count": len(events),
            "event_types": event_type_labels,
            "meta_label": f"{', '.join(event_type_labels)} · {len(events)} event{'s' if len(events) != 1 else ''}",
            "recovered_amount": round(recovered_amount, 2),
            "recovered_amount_display": f"₹{recovered_amount:,.2f}",
            "contacts_label": (
                f"{contact_count_7d} this week"
                + (" (capped)" if contact_count_7d >= 3 else "")
            ),
        })

    return {"customers": customers, "total": len(customers)}


@router.get("/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db)) -> dict:
    """Return detailed customer profile with recent audit history."""
    events = db.execute(
        select(EventRow)
        .where(EventRow.customer_id == customer_id)
        .order_by(EventRow.timestamp.desc())
    ).scalars().all()
    if not events:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id!r} not found")

    history = db.get(CustomerHistoryRow, customer_id)
    event_ids = [e.event_id for e in events]
    audit_rows = []
    if event_ids:
        audit_rows = db.execute(
            select(AuditLogRow)
            .where(AuditLogRow.event_id.in_(event_ids))
            .order_by(AuditLogRow.timestamp.desc())
        ).scalars().all()

    recovered_event_ids = {r.event_id for r in audit_rows if r.action_taken in RECOVERY_ACTIONS}
    recovered_amount = sum(e.amount for e in events if e.event_id in recovered_event_ids)

    return {
        "customer_id": customer_id,
        "initials": _customer_initials(customer_id),
        "preferred_channel": history.preferred_channel if history else "sms",
        "reliability": _reliability_label(history.past_failures if history else None),
        "contact_count_7d": history.contact_count_7d if history else 0,
        "past_failures": history.past_failures if history else 0,
        "last_contacted_at": history.last_contacted_at.isoformat() if history and history.last_contacted_at else None,
        "event_count": len(events),
        "recovered_amount": round(recovered_amount, 2),
        "recovered_amount_display": f"₹{recovered_amount:,.2f}",
        "recent_audit": [
            {
                "id": r.id,
                "event_id": r.event_id,
                "action_taken": r.action_taken,
                "reasoning": r.reasoning,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in audit_rows[:10]
        ],
    }


def _reliability_label(past_failures: int | None) -> str:
    if past_failures is None:
        return "Unknown"
    if past_failures == 0:
        return "Good"
    elif past_failures <= 2:
        return "Fair"
    else:
        return "Poor"
