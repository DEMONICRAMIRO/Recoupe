import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import PipelineState
from app.core.config import settings
from app.core.live_status import update_live_status
from app.core.risk_gate import GateDecision, evaluate, evaluate_call
from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow, utc_now
from app.db.session import SessionLocal
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType
from app.services.comms import SendResult
from app.services.comms.email import send_email
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.renewal_notice import RENEWAL_EXPIRY_EMAIL, RENEWAL_EXPIRY_SMS
from app.services.comms.templates.renewal_voice import VOICE_RENEWAL_HINGLISH
from app.services.comms.voice import place_voice_call
from app.services.comms.whatsapp import send_whatsapp

logger = logging.getLogger(__name__)

AGENT_NAME = "renewal_agent"
RENEWAL_STAGES = ("fetch", "enrich", "diagnose", "gate", "decide", "execute_log")

CAUSE_BY_REASON_CODE = {
    "card_expired": "card_expiry",
    "expired_card": "card_expiry",
    "insufficient_funds": "funds",
    "processor_error": "processor_error",
    "bank_timeout": "processor_error",
    "mandate_revoked": "processor_error",
}

ACTION_BY_CAUSE = {
    "card_expiry": "prompt_card_update",
    "funds": "grace_period",
    "processor_error": "silent_retry",
}


@dataclass(frozen=True)
class RenewalDiagnosis:
    cause: str
    reasoning: str


@dataclass(frozen=True)
class RenewalDecision:
    action: str
    reasoning: str
    voice_escalate: bool = False


class RenewalPipelineState(TypedDict, total=False):
    event: Event
    db: Session
    customer_history: CustomerHistoryRow | None
    diagnosis: RenewalDiagnosis
    gate: GateDecision
    decision: RenewalDecision
    deliveries: list[dict]
    voice_result: SendResult | None
    response: SubAgentResponse
    stage_trace: list[str]
    audit_log_id: int


def _trace(state: RenewalPipelineState, stage: str) -> list[str]:
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
    db.flush()


def fetch_event(state: RenewalPipelineState) -> dict:
    event = state["event"]
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "fetch", agent=AGENT_NAME)
    if event.event_type is not EventType.renewal_failed:
        raise ValueError("Renewal Agent accepts only renewal_failed events")
    _persist_event(state["db"], event)
    return {"stage_trace": _trace(state, "fetch")}


def enrich_context(state: RenewalPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "enrich", agent=AGENT_NAME)
    history = state["db"].get(CustomerHistoryRow, state["event"].customer_id)
    return {"customer_history": history, "stage_trace": _trace(state, "enrich")}


def diagnose_renewal(state: RenewalPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "classify", agent=AGENT_NAME)
    event = state["event"]
    code = (event.reason_code or "").strip().lower()
    cause = CAUSE_BY_REASON_CODE.get(code, "processor_error")
    diagnosis = RenewalDiagnosis(
        cause=cause,
        reasoning=f"Rule mapped renewal decline code {code!r} to cause {cause!r}.",
    )
    return {"diagnosis": diagnosis, "stage_trace": _trace(state, "diagnose")}


def gate_renewal(state: RenewalPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "gate", agent=AGENT_NAME)
    gate = evaluate(state.get("customer_history"))
    return {"gate": gate, "stage_trace": _trace(state, "gate")}


def decide_renewal(state: RenewalPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "decide", agent=AGENT_NAME)
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    event = state["event"]
    history = state.get("customer_history")

    if not gate.allowed:
        decision = RenewalDecision(
            action="hold",
            reasoning=f"Decision held because the risk gate blocked the event: {gate.reason}",
        )
    else:
        action = ACTION_BY_CAUSE[diagnosis.cause]
        ltv = float(event.metadata.get("ltv_amount") or 0)
        past_failures = history.past_failures if history else 0
        voice_escalate = ltv >= settings.renewal_ltv_call_threshold or past_failures >= 3
        decision = RenewalDecision(
            action=action,
            reasoning=(
                f"Cause {diagnosis.cause!r} maps to action {action!r}; {gate.reason}."
            ),
            voice_escalate=voice_escalate,
        )
    return {"decision": decision, "stage_trace": _trace(state, "decide")}


def _dispatch_multi_channel(event: Event) -> list[dict]:
    phone = event.metadata.get("phone")
    email = event.metadata.get("email")
    customer_name = event.metadata.get("customer_name", event.customer_id)
    values = {
        "customer_name": customer_name,
        "plan": event.metadata.get("plan", "subscription"),
        "link": event.metadata.get("payment_link", f"{settings.payment_link_base_url}/{event.event_id}"),
    }
    deliveries: list[dict] = []

    sms_rendered = render(RENEWAL_EXPIRY_SMS, **values)
    if phone:
        sms_result = send_sms(phone, sms_rendered.body)
        if not sms_result.success:
            logger.error("renewal_agent: sms delivery failed for event %s: %s", event.event_id, sms_result.error)
        deliveries.append({"channel": "sms", "result": sms_result.as_dict()})
    else:
        deliveries.append({"channel": "sms", "status": "not_sent_missing_recipient"})

    email_rendered = render(RENEWAL_EXPIRY_EMAIL, **values)
    if email:
        email_result = send_email(email, email_rendered.subject or "Recoupe", email_rendered.body)
        if not email_result.success:
            logger.error("renewal_agent: email delivery failed for event %s: %s", event.event_id, email_result.error)
        deliveries.append({"channel": "email", "result": email_result.as_dict()})
    else:
        deliveries.append({"channel": "email", "status": "not_sent_missing_recipient"})

    if phone:
        whatsapp_result = send_whatsapp(phone, sms_rendered.body)
        if not whatsapp_result.success:
            logger.error("renewal_agent: whatsapp delivery failed for event %s: %s", event.event_id, whatsapp_result.error)
        deliveries.append({"channel": "whatsapp_fallback", "result": whatsapp_result.as_dict()})
    else:
        deliveries.append({"channel": "whatsapp_fallback", "status": "not_sent_missing_recipient"})

    return deliveries


def _call_history(db: Session, customer_id: str, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(timezone.utc)
    event_ids = select(EventRow.event_id).where(EventRow.customer_id == customer_id)
    rows = db.execute(
        select(AuditLogRow).where(
            AuditLogRow.event_id.in_(event_ids),
            AuditLogRow.action_taken.like("%voice_call%"),
        )
    ).scalars().all()
    recent = [row for row in rows if row.timestamp and (current_time - row.timestamp) <= timedelta(days=7)]
    last_call_at = max((row.timestamp for row in recent), default=None)
    return {"call_count_7d": len(recent), "last_call_at": last_call_at}


def _execute_voice_escalation(
    db: Session,
    event: Event,
    decision: RenewalDecision,
) -> SendResult | None:
    if not decision.voice_escalate:
        return None
    phone = event.metadata.get("phone")
    if not phone:
        return None
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
    values = {
        "customer_name": customer_name,
        "plan": event.metadata.get("plan", "subscription"),
    }
    script = render(VOICE_RENEWAL_HINGLISH, **values).body
    voice_result = place_voice_call(phone, script, url_params={"Name": customer_name})
    if not voice_result.success:
        logger.error("renewal_agent: voice delivery failed for event %s: %s", event.event_id, voice_result.error)
    db.add(
        AuditLogRow(
            event_id=event.event_id,
            action_taken="voice_call",
            reasoning=(
                f"call_gate={gate_call.reason}; delivery={voice_result.status}"
                + (f"({voice_result.error})" if voice_result.error else "")
            ),
            timestamp=utc_now(),
        )
    )
    db.flush()
    return voice_result


def _delivery_str(item: dict) -> str:
    result = item.get("result")
    if result:
        error = result.get("error")
        return f"{result.get('status', 'unknown')}({error})" if error else result.get("status", "unknown")
    return item.get("status", "unknown")


def _write_audit(
    db: Session,
    event: Event,
    diagnosis: RenewalDiagnosis,
    gate: GateDecision,
    decision: RenewalDecision,
    deliveries: list[dict],
    voice_result: SendResult | None,
) -> int:
    delivery_summary = "; ".join(_delivery_str(item) for item in deliveries)
    if voice_result and voice_result.error:
        voice_delivery = f"{voice_result.status}({voice_result.error})"
    else:
        voice_delivery = voice_result.status if voice_result else "not_placed"
    reasoning = (
        f"cause={diagnosis.cause}; diagnosis={diagnosis.reasoning}; gate={gate.reason}; "
        f"decision={decision.reasoning}; deliveries={delivery_summary}; "
        f"voice_escalated={decision.voice_escalate}; voice_delivery="
        f"{voice_delivery}"
    )
    row = db.execute(
        select(AuditLogRow)
        .where(AuditLogRow.event_id == event.event_id)
        .order_by(AuditLogRow.id.desc())
    ).scalars().first()
    if row is None:
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


def execute_and_log(state: RenewalPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "execute_log", agent=AGENT_NAME)
    event = state["event"]
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    decision = state["decision"]

    if decision.action == "hold":
        deliveries = [{"channel": "blocked", "status": "hold_no_dispatch"}]
        voice_result = None
    else:
        deliveries = _dispatch_multi_channel(event)
        voice_result = _execute_voice_escalation(state["db"], event, decision)

    audit_log_id = _write_audit(
        state["db"], event, diagnosis, gate, decision, deliveries, voice_result
    )
    trace = _trace(state, "execute_log")
    response = SubAgentResponse(
        agent=AGENT_NAME,
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=decision.action,
        reasoning=f"{diagnosis.reasoning} {decision.reasoning}",
        status="processed",
        cause=diagnosis.cause,
        gate_allowed=gate.allowed,
        gate_reason=gate.reason,
        stage_trace=trace,
        delivery_status="; ".join(_delivery_str(item) for item in deliveries),
    )
    return {
        "deliveries": deliveries,
        "voice_result": voice_result,
        "audit_log_id": audit_log_id,
        "stage_trace": trace,
        "response": response,
    }


def build_renewal_pipeline():
    builder = StateGraph(RenewalPipelineState)
    builder.add_node("fetch", fetch_event)
    builder.add_node("enrich", enrich_context)
    builder.add_node("diagnose", diagnose_renewal)
    builder.add_node("gate", gate_renewal)
    builder.add_node("decide", decide_renewal)
    builder.add_node("execute_log", execute_and_log)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "enrich")
    builder.add_edge("enrich", "diagnose")
    builder.add_edge("diagnose", "gate")
    builder.add_edge("gate", "decide")
    builder.add_edge("decide", "execute_log")
    builder.add_edge("execute_log", END)
    return builder.compile()


renewal_pipeline = build_renewal_pipeline()


def run_renewal_event(event: Event, db: Session | None = None) -> dict:
    owns_session = db is None
    active_db = db or SessionLocal()
    try:
        return renewal_pipeline.invoke({"event": event, "db": active_db})
    except Exception:
        active_db.rollback()
        raise
    finally:
        if owns_session:
            active_db.close()


def renewal_agent(state: PipelineState) -> dict:
    result = run_renewal_event(state["event"], state.get("db"))
    return {
        "response": result["response"],
        "renewal_result": result["response"],
        "audit_log_id": result["audit_log_id"],
    }
