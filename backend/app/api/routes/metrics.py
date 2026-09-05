"""metrics.py — Dashboard summary statistics endpoint.

All figures are computed from real audit_log + events tables, never hardcoded.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRow, EventRow
from app.db.session import SessionLocal

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Actions that count as a successful recovery (payment retrieved, comms sent)
RECOVERY_ACTIONS = {
    "retry_now",
    "retry_alt_route",
    "send_payment_link",
    "send_reminder",
    "send_firm_followup",
    "escalate_with_call",
    "escalate_no_call",
    "nudge",
    "nudge_discount",
    "plain",
    "discount",
    "prompt_card_update",
    "grace_period",
    "silent_retry",
}

# Actions that count as gate-blocked
BLOCKED_ACTIONS = {"hold", "voice_call_blocked"}

# Map event_type -> agent name (matches AGENT_NAME constants in each agent)
AGENT_BY_EVENT_TYPE = {
    "payment_failed": "payment_agent",
    "cart_abandoned": "cart_agent",
    "renewal_failed": "renewal_agent",
    "invoice_overdue": "invoice_agent",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _week_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=7)


@router.get("/summary")
def summary_metrics(db: Session = Depends(get_db)) -> dict:
    """Return dashboard hero stats computed from real DB rows."""
    since = _week_start()

    # All audit rows in the last 7 days
    rows = db.execute(
        select(AuditLogRow)
        .where(AuditLogRow.timestamp >= since)
        .order_by(AuditLogRow.timestamp.desc())
    ).scalars().all()

    # Total events processed (unique event IDs with at least one audit row)
    events_processed = len({r.event_id for r in rows})

    # Blocked by gate
    blocked_by_gate = sum(1 for r in rows if r.action_taken in BLOCKED_ACTIONS)

    # For amount recovered, join with events table
    recovered_rows = [r for r in rows if r.action_taken in RECOVERY_ACTIONS]
    recovered_event_ids = list({r.event_id for r in recovered_rows})

    recovered_amount = 0.0
    if recovered_event_ids:
        event_amounts = db.execute(
            select(EventRow.event_id, EventRow.amount)
            .where(EventRow.event_id.in_(recovered_event_ids))
        ).all()
        recovered_amount = sum(row.amount for row in event_amounts)

    # Recovery rate
    recovery_rate = (
        len({r.event_id for r in recovered_rows}) / events_processed * 100
        if events_processed > 0
        else 0.0
    )

    # Per-agent breakdown
    per_agent: dict[str, dict] = {}
    for event_type, agent_name in AGENT_BY_EVENT_TYPE.items():
        agent_event_ids = set()
        agent_recovered_ids = set()

        # Get events of this type in window
        agent_events = db.execute(
            select(EventRow.event_id)
            .where(EventRow.event_type == event_type)
            .where(EventRow.timestamp >= since)
        ).scalars().all()
        agent_event_ids = set(agent_events)

        if not agent_event_ids:
            per_agent[agent_name] = {
                "events": 0,
                "recovered": 0,
                "blocked": 0,
                "recovery_rate_pct": 0.0,
                "recovered_amount": 0.0,
            }
            continue

        # Audit rows for these events
        agent_audit = db.execute(
            select(AuditLogRow)
            .where(AuditLogRow.event_id.in_(agent_event_ids))
        ).scalars().all()

        agent_recovered_ids = {r.event_id for r in agent_audit if r.action_taken in RECOVERY_ACTIONS}
        agent_blocked = sum(1 for r in agent_audit if r.action_taken in BLOCKED_ACTIONS)

        # Amount for recovered events
        agent_amounts = 0.0
        if agent_recovered_ids:
            agent_amount_rows = db.execute(
                select(EventRow.amount)
                .where(EventRow.event_id.in_(agent_recovered_ids))
            ).scalars().all()
            agent_amounts = sum(agent_amount_rows)

        per_agent[agent_name] = {
            "events": len(agent_event_ids),
            "recovered": len(agent_recovered_ids),
            "blocked": agent_blocked,
            "recovery_rate_pct": (
                len(agent_recovered_ids) / len(agent_event_ids) * 100
                if agent_event_ids
                else 0.0
            ),
            "recovered_amount": round(agent_amounts, 2),
        }

    return {
        "window_days": 7,
        "events_processed": events_processed,
        "blocked_by_gate": blocked_by_gate,
        "recovered_this_week": round(recovered_amount, 2),
        "recovery_rate_pct": round(recovery_rate, 1),
        "per_agent": per_agent,
    }


# Keep the old /batch endpoint for backwards compatibility (Phase 1-4 stubs)
@router.get("/batch")
def batch_metrics(db: Session = Depends(get_db)) -> dict:
    return summary_metrics(db)
