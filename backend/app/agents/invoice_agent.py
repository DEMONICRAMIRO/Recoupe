"""invoice_agent.py — Full six-stage pipeline for invoice_overdue events.

Stages (per architecture.md Section 4):
  Fetch → Enrich → Classify → Gate → Decide → Execute & Log

New logic vs earlier agents:
  - Classify uses deterministic days-overdue bucketing (no LLM — clean signal).
  - Decide incorporates call_priority.py score (hybrid rules+LLM, isolated module).
  - Gate uses evaluate_invoice_gate() — an independent counter, not shared with
    Payment/Renewal/Cart caps (same principle Phase 3 established for call-frequency gate).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import PipelineState
from app.core.call_priority import CallPriorityResult, score_call_priority
from app.core.config import settings
from app.core.live_status import update_live_status
from app.core.risk_gate import GateDecision, evaluate_invoice_gate, evaluate_call
from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow, utc_now
from app.db.session import SessionLocal
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType
from app.services.comms import SendResult
from app.services.comms.email import send_email
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.invoice_reminder import (
    INVOICE_ESCALATION_EMAIL,
    INVOICE_ESCALATION_SMS,
    INVOICE_FIRM_FOLLOWUP_EMAIL,
    INVOICE_FIRM_FOLLOWUP_SMS,
    INVOICE_REMINDER_EMAIL,
    INVOICE_REMINDER_SMS,
)
from app.services.comms.voice import place_voice_call

logger = logging.getLogger(__name__)

AGENT_NAME = "invoice_agent"
INVOICE_STAGES = ("fetch", "enrich", "classify", "gate", "decide", "execute_log")

# Days-overdue bucket thresholds
BUCKET_30 = "overdue_30"   # 1–59 days
BUCKET_60 = "overdue_60"   # 60–89 days
BUCKET_90 = "overdue_90"   # 90+ days

# Actions
ACTION_SEND_REMINDER = "send_reminder"
ACTION_SEND_FIRM_FOLLOWUP = "send_firm_followup"
ACTION_ESCALATE_WITH_CALL = "escalate_with_call"
ACTION_ESCALATE_NO_CALL = "escalate_no_call"
ACTION_HOLD = "hold"

PAYMENT_LINK_BASE = settings.payment_link_base_url


@dataclass(frozen=True)
class InvoiceClassification:
    bucket: str          # BUCKET_30 / BUCKET_60 / BUCKET_90
    days_overdue: int
    reasoning: str


@dataclass(frozen=True)
class InvoiceDecision:
    action: str
    reasoning: str
    voice_escalate: bool = False


class InvoicePipelineState(TypedDict, total=False):
    event: Event
    db: Session
    customer_history: CustomerHistoryRow | None
    classification: InvoiceClassification
    call_priority: CallPriorityResult
    gate: GateDecision
    decision: InvoiceDecision
    deliveries: list[dict]
    voice_result: SendResult | None
    response: SubAgentResponse
    stage_trace: list[str]
    audit_log_id: int


def _trace(state: InvoicePipelineState, stage: str) -> list[str]:
    return [*state.get("stage_trace", []), stage]


def _persist_event(db: Session, event: Event) -> None:
    row = db.get(EventRow, event.event_id)
    if row is None:
        db.add(
            EventRow(
                event_id=event.event_id,
                event_type=event.event_type.value,
                customer_id=event.customer_id,
                amount=event.amount,
                currency=event.currency,
                reason_code=event.reason_code,
                timestamp=event.timestamp,
                event_metadata=event.metadata,
            )
        )
    else:
        row.event_type = event.event_type.value
        row.customer_id = event.customer_id
        row.amount = event.amount
        row.currency = event.currency
        row.reason_code = event.reason_code
        row.timestamp = event.timestamp
        row.event_metadata = event.metadata


# ---------------------------------------------------------------------------
# Stage 1: Fetch
# ---------------------------------------------------------------------------

def fetch_event(state: InvoicePipelineState) -> dict:
    event = state["event"]
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "fetch", agent=AGENT_NAME)
    db = state["db"]
    _persist_event(db, event)
    db.flush()
    logger.info("%s fetched event %s (%s)", AGENT_NAME, event.event_id, event.event_type.value)
    return {"stage_trace": _trace(state, "fetch")}


# ---------------------------------------------------------------------------
# Stage 2: Enrich
# ---------------------------------------------------------------------------

def enrich_context(state: InvoicePipelineState) -> dict:
    """Plain DB lookup — no LLM, per architecture non-negotiable."""
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "enrich", agent=AGENT_NAME)
    event = state["event"]
    db = state["db"]
    history = db.get(CustomerHistoryRow, event.customer_id)
    logger.info(
        "%s enriched customer %s: history=%s",
        AGENT_NAME,
        event.customer_id,
        "found" if history else "not_found",
    )
    return {"customer_history": history, "stage_trace": _trace(state, "enrich")}


# ---------------------------------------------------------------------------
# Stage 3: Classify — deterministic days-overdue bucketing (no LLM)
# ---------------------------------------------------------------------------

def classify_invoice(state: InvoicePipelineState) -> dict:
    """Rules-only bucketing. Days overdue is a clean signal — no LLM needed."""
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "classify", agent=AGENT_NAME)
    event = state["event"]
    days_overdue = int(event.metadata.get("days_overdue", 0))

    if days_overdue >= 90:
        bucket = BUCKET_90
        reasoning = f"{days_overdue}d overdue → 90+ bucket (highest urgency)"
    elif days_overdue >= 60:
        bucket = BUCKET_60
        reasoning = f"{days_overdue}d overdue → 60–89 bucket (firm follow-up)"
    else:
        bucket = BUCKET_30
        reasoning = f"{days_overdue}d overdue → <60 bucket (initial reminder)"

    classification = InvoiceClassification(
        bucket=bucket,
        days_overdue=days_overdue,
        reasoning=reasoning,
    )
    logger.info("%s classified event %s: %s", AGENT_NAME, event.event_id, bucket)

    # Score call priority here (hybrid rules+LLM, isolated in call_priority.py)
    account_reliability = str(event.metadata.get("account_reliability", "fair"))
    broken_promise = bool(event.metadata.get("broken_promise", False))
    priority = score_call_priority(
        days_overdue=days_overdue,
        account_reliability=account_reliability,
        broken_promise=broken_promise,
    )
    logger.info(
        "%s call-priority for %s: tier=%s score=%.2f llm=%s",
        AGENT_NAME, event.event_id, priority.tier, priority.score, priority.llm_invoked,
    )

    return {
        "classification": classification,
        "call_priority": priority,
        "stage_trace": _trace(state, "classify"),
    }


# ---------------------------------------------------------------------------
# Stage 4: Gate — independent invoice escalation counter
# ---------------------------------------------------------------------------

def _invoice_contact_history(db: Session, customer_id: str, now: datetime | None = None) -> dict:
    """Count invoice-related audit log entries for this customer in the past 30 days."""
    current_time = now or datetime.now(timezone.utc)
    event_ids_q = select(EventRow.event_id).where(
        EventRow.customer_id == customer_id,
        EventRow.event_type == EventType.invoice_overdue.value,
    )
    rows = db.execute(
        select(AuditLogRow).where(AuditLogRow.event_id.in_(event_ids_q))
    ).scalars().all()
    recent = [
        row for row in rows
        if row.timestamp and (current_time - row.timestamp) <= timedelta(days=30)
    ]
    last_contact = max((r.timestamp for r in recent), default=None)
    return {
        "invoice_escalation_count_30d": len(recent),
        "last_invoice_contact_at": last_contact,
    }


def gate_invoice(state: InvoicePipelineState) -> dict:
    """Hard-coded gate using Invoice's independent counters — never an LLM call."""
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "gate", agent=AGENT_NAME)
    event = state["event"]
    db = state["db"]
    invoice_info = _invoice_contact_history(db, event.customer_id)
    gate = evaluate_invoice_gate(invoice_info)
    logger.info("%s gate for %s: %s", AGENT_NAME, event.event_id, gate.reason)
    return {"gate": gate, "stage_trace": _trace(state, "gate")}


# ---------------------------------------------------------------------------
# Stage 5: Decide
# ---------------------------------------------------------------------------

def decide_invoice(state: InvoicePipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "decide", agent=AGENT_NAME)
    gate = state["gate"]
    classification = state["classification"]
    priority = state["call_priority"]

    if not gate.allowed:
        decision = InvoiceDecision(
            action=ACTION_HOLD,
            reasoning=f"Gate blocked: {gate.reason}",
            voice_escalate=False,
        )
    elif classification.bucket == BUCKET_90:
        voice_escalate = priority.tier == "call"
        action = ACTION_ESCALATE_WITH_CALL if voice_escalate else ACTION_ESCALATE_NO_CALL
        decision = InvoiceDecision(
            action=action,
            reasoning=(
                f"{classification.reasoning}; priority={priority.tier} "
                f"(score={priority.score:.2f}); gate={gate.reason}"
            ),
            voice_escalate=voice_escalate,
        )
    elif classification.bucket == BUCKET_60:
        decision = InvoiceDecision(
            action=ACTION_SEND_FIRM_FOLLOWUP,
            reasoning=(
                f"{classification.reasoning}; priority={priority.tier}; gate={gate.reason}"
            ),
            voice_escalate=False,
        )
    else:  # BUCKET_30
        decision = InvoiceDecision(
            action=ACTION_SEND_REMINDER,
            reasoning=(
                f"{classification.reasoning}; priority={priority.tier}; gate={gate.reason}"
            ),
            voice_escalate=False,
        )

    logger.info(
        "%s decision for %s: action=%s voice=%s",
        AGENT_NAME, state["event"].event_id, decision.action, decision.voice_escalate,
    )
    return {"decision": decision, "stage_trace": _trace(state, "decide")}


# ---------------------------------------------------------------------------
# Stage 6: Execute & Log
# ---------------------------------------------------------------------------

def _build_template_values(event: Event) -> dict:
    amount_str = f"{event.amount:,.2f}"
    return {
        "customer_name": event.metadata.get("customer_name", event.customer_id),
        "invoice_number": event.metadata.get("invoice_number", "N/A"),
        "amount": amount_str,
        "days_overdue": str(event.metadata.get("days_overdue", 0)),
        "due_date": event.metadata.get("due_date", ""),
        "link": f"{PAYMENT_LINK_BASE}/{event.event_id}",
    }


def _dispatch_invoice_channels(
    event: Event,
    decision: InvoiceDecision,
    classification: InvoiceClassification,
) -> list[dict]:
    phone = event.metadata.get("phone")
    email_addr = event.metadata.get("email")
    values = _build_template_values(event)
    deliveries: list[dict] = []

    # Select templates based on action
    if decision.action in (ACTION_ESCALATE_WITH_CALL, ACTION_ESCALATE_NO_CALL):
        sms_tmpl = INVOICE_ESCALATION_SMS
        email_tmpl = INVOICE_ESCALATION_EMAIL
    elif decision.action == ACTION_SEND_FIRM_FOLLOWUP:
        sms_tmpl = INVOICE_FIRM_FOLLOWUP_SMS
        email_tmpl = INVOICE_FIRM_FOLLOWUP_EMAIL
    else:  # send_reminder
        sms_tmpl = INVOICE_REMINDER_SMS
        email_tmpl = INVOICE_REMINDER_EMAIL

    # SMS
    sms_rendered = render(sms_tmpl, **values)
    if phone:
        sms_result = send_sms(phone, sms_rendered.body)
        deliveries.append({"channel": "sms", "result": sms_result.as_dict()})
    else:
        deliveries.append({"channel": "sms", "status": "not_sent_missing_recipient"})

    # Email
    email_rendered = render(email_tmpl, **values)
    if email_addr:
        email_result = send_email(
            email_addr,
            email_rendered.subject or f"Invoice {values['invoice_number']} — Overdue",
            email_rendered.body,
        )
        deliveries.append({"channel": "email", "result": email_result.as_dict()})
    else:
        deliveries.append({"channel": "email", "status": "not_sent_missing_recipient"})

    return deliveries


def _execute_voice_escalation(
    db: Session,
    event: Event,
    decision: InvoiceDecision,
) -> SendResult | None:
    if not decision.voice_escalate:
        return None
    phone = event.metadata.get("phone")
    if not phone:
        return None

    # Check call-frequency gate (independent of invoice escalation gate)
    call_info = _call_history(db, event.customer_id)
    gate_call = evaluate_call(call_info)
    if not gate_call.allowed:
        db.add(
            AuditLogRow(
                event_id=event.event_id,
                action_taken="voice_call_blocked",
                reasoning=f"call_gate={gate_call.reason}",
                timestamp=utc_now(),
            )
        )
        db.flush()
        return None

    customer_name = event.metadata.get("customer_name", event.customer_id)
    invoice_number = event.metadata.get("invoice_number", "N/A")
    amount_str = f"{event.amount:,.2f}"
    script = (
        f"Namaste {customer_name}. Aapka invoice number {invoice_number} "
        f"ke liye INR {amount_str} ka payment abhi tak nahi aaya hai. "
        "Kripya turant payment karein ya humse baat karein."
    )
    voice_result = place_voice_call(phone, script, url_params={"Name": customer_name})
    db.add(
        AuditLogRow(
            event_id=event.event_id,
            action_taken="voice_call",
            reasoning=f"call_gate={gate_call.reason}; delivery={voice_result.status}",
            timestamp=utc_now(),
        )
    )
    db.flush()
    return voice_result


def _call_history(db: Session, customer_id: str, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(timezone.utc)
    event_ids = select(EventRow.event_id).where(EventRow.customer_id == customer_id)
    rows = db.execute(
        select(AuditLogRow).where(
            AuditLogRow.event_id.in_(event_ids),
            AuditLogRow.action_taken.like("%voice_call%"),
        )
    ).scalars().all()
    recent = [r for r in rows if r.timestamp and (current_time - r.timestamp) <= timedelta(days=7)]
    last_call_at = max((r.timestamp for r in recent), default=None)
    return {"call_count_7d": len(recent), "last_call_at": last_call_at}


def _write_audit(
    db: Session,
    event: Event,
    classification: InvoiceClassification,
    priority: CallPriorityResult,
    gate: GateDecision,
    decision: InvoiceDecision,
    deliveries: list[dict],
    voice_result: SendResult | None,
) -> int:
    delivery_summary = "; ".join(
        item.get("result", {}).get("status") or item.get("status", "unknown")
        for item in deliveries
    )
    reasoning = (
        f"bucket={classification.bucket}; days_overdue={classification.days_overdue}; "
        f"priority_tier={priority.tier}; priority_score={priority.score:.2f}; "
        f"priority_llm={priority.llm_invoked}; gate={gate.reason}; "
        f"decision={decision.reasoning}; deliveries={delivery_summary}; "
        f"voice_escalated={decision.voice_escalate}; "
        f"voice_delivery={voice_result.status if voice_result else 'not_placed'}"
    )
    # One canonical audit row per event (upsert pattern matching renewal_agent)
    row = db.execute(
        select(AuditLogRow)
        .where(AuditLogRow.event_id == event.event_id)
        .order_by(AuditLogRow.id.desc())
    ).scalars().first()
    if row is None or row.action_taken in ("voice_call", "voice_call_blocked"):
        # Always write the main decision row
        row = AuditLogRow(
            event_id=event.event_id,
            action_taken=decision.action,
            reasoning=reasoning,
            timestamp=utc_now(),
        )
        db.add(row)
    else:
        row.action_taken = decision.action
        row.reasoning = reasoning
        row.timestamp = utc_now()
    db.flush()
    db.commit()
    return row.id


def execute_and_log(state: InvoicePipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "execute_log", agent=AGENT_NAME)
    event = state["event"]
    classification = state["classification"]
    priority = state["call_priority"]
    gate = state["gate"]
    decision = state["decision"]
    db = state["db"]

    if decision.action == ACTION_HOLD:
        deliveries = [{"channel": "blocked", "status": "hold_no_dispatch"}]
        voice_result = None
    else:
        deliveries = _dispatch_invoice_channels(event, decision, classification)
        voice_result = _execute_voice_escalation(db, event, decision)

    audit_log_id = _write_audit(db, event, classification, priority, gate, decision, deliveries, voice_result)
    trace = _trace(state, "execute_log")

    response = SubAgentResponse(
        agent=AGENT_NAME,
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=decision.action,
        reasoning=f"{classification.reasoning}; {decision.reasoning}",
        status="processed",
        gate_allowed=gate.allowed,
        gate_reason=gate.reason,
        stage_trace=trace,
        delivery_status="; ".join(
            item.get("result", {}).get("status") or item.get("status", "unknown")
            for item in deliveries
        ),
    )
    return {
        "deliveries": deliveries,
        "voice_result": voice_result,
        "audit_log_id": audit_log_id,
        "stage_trace": trace,
        "response": response,
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_invoice_pipeline():
    builder = StateGraph(InvoicePipelineState)
    builder.add_node("fetch", fetch_event)
    builder.add_node("enrich", enrich_context)
    builder.add_node("classify", classify_invoice)
    builder.add_node("gate", gate_invoice)
    builder.add_node("decide", decide_invoice)
    builder.add_node("execute_log", execute_and_log)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "enrich")
    builder.add_edge("enrich", "classify")
    builder.add_edge("classify", "gate")
    builder.add_edge("gate", "decide")
    builder.add_edge("decide", "execute_log")
    builder.add_edge("execute_log", END)
    return builder.compile()


invoice_pipeline = build_invoice_pipeline()


def run_invoice_event(event: Event, db: Session | None = None) -> dict:
    owns_session = db is None
    active_db = db or SessionLocal()
    try:
        return invoice_pipeline.invoke({"event": event, "db": active_db})
    except Exception:
        active_db.rollback()
        raise
    finally:
        if owns_session:
            active_db.close()


def invoice_agent(state: PipelineState) -> dict:
    result = run_invoice_event(state["event"], state.get("db"))
    return {
        "response": result["response"],
        "invoice_result": result["response"],
        "audit_log_id": result["audit_log_id"],
    }
