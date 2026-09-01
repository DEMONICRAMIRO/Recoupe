import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import PipelineState
from app.core.config import settings
from app.core.llm_client import get_llm
from app.core.risk_gate import GateDecision, evaluate
from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow, utc_now
from app.db.session import SessionLocal
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType
from app.services.comms import SendResult
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.payment_link import (
    PAYMENT_LINK_SMS,
    PAYMENT_LINK_WHATSAPP,
)
from app.services.comms.whatsapp import send_whatsapp

logger = logging.getLogger(__name__)

AGENT_NAME = "payment_agent"
PAYMENT_STAGES = (
    "fetch",
    "enrich",
    "classify",
    "gate",
    "decide",
    "execute_log",
)

CAUSE_BY_REASON_CODE = {
    "insufficient_funds": "insufficient_funds",
    "card_expired": "expired_card",
    "expired_card": "expired_card",
    "auth_declined": "3ds_failure",
    "3ds_failed": "3ds_failure",
    "authentication_failed": "3ds_failure",
    "processor_error": "bank_timeout",
    "bank_timeout": "bank_timeout",
    "timeout": "bank_timeout",
    "network_error": "network_issue",
    "network_issue": "network_issue",
}

ACTION_BY_CAUSE = {
    "insufficient_funds": "retry_alt_route",
    "bank_timeout": "retry_now",
    "network_issue": "retry_now",
    "expired_card": "send_payment_link",
    "3ds_failure": "send_payment_link",
    "ambiguous": "send_payment_link",
}


@dataclass(frozen=True)
class PaymentDiagnosis:
    cause: str
    reasoning: str


@dataclass(frozen=True)
class PaymentDecision:
    action: str
    reasoning: str


class PaymentPipelineState(TypedDict, total=False):
    event: Event
    db: Session
    customer_history: CustomerHistoryRow | None
    diagnosis: PaymentDiagnosis
    gate: GateDecision
    decision: PaymentDecision
    delivery: SendResult | None
    delivery_status: str
    response: SubAgentResponse
    stage_trace: list[str]
    audit_log_id: int


def _trace(state: PaymentPipelineState, stage: str) -> list[str]:
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


def fetch_event(state: PaymentPipelineState) -> dict:
    event = state["event"]
    if event.event_type is not EventType.payment_failed:
        raise ValueError("Payment Agent accepts only payment_failed events")
    _persist_event(state["db"], event)
    return {"stage_trace": _trace(state, "fetch")}


def enrich_context(state: PaymentPipelineState) -> dict:
    history = state["db"].get(CustomerHistoryRow, state["event"].customer_id)
    return {"customer_history": history, "stage_trace": _trace(state, "enrich")}


def _ambiguous_diagnosis(event: Event, history: CustomerHistoryRow | None) -> PaymentDiagnosis:
    history_count = history.past_failures if history is not None else 0
    nuance = None
    try:
        model = get_llm()
    except NotImplementedError:
        model = None
    if model is not None and hasattr(model, "invoke"):
        prompt = (
            "Assess this ambiguous payment failure conservatively. "
            f"Reason code={event.reason_code!r}; past_failures={history_count}."
        )
        answer = model.invoke(prompt)
        nuance = getattr(answer, "content", str(answer))

    if nuance:
        reasoning = f"Ambiguous signal; LLM nuance: {nuance}"
    else:
        reasoning = (
            f"Ambiguous payment signal for code {event.reason_code!r}; "
            f"customer history shows {history_count} past failures, so the event "
            "uses the bounded payment-link path."
        )
    return PaymentDiagnosis(cause="ambiguous", reasoning=reasoning)


def classify_payment(state: PaymentPipelineState) -> dict:
    event = state["event"]
    code = (event.reason_code or "").strip().lower()
    cause = CAUSE_BY_REASON_CODE.get(code)
    if cause is None:
        diagnosis = _ambiguous_diagnosis(event, state.get("customer_history"))
    else:
        diagnosis = PaymentDiagnosis(
            cause=cause,
            reasoning=f"Rule mapped decline code {code!r} to cause {cause!r}.",
        )
    return {"diagnosis": diagnosis, "stage_trace": _trace(state, "classify")}


def gate_payment(state: PaymentPipelineState) -> dict:
    gate = evaluate(state.get("customer_history"))
    return {"gate": gate, "stage_trace": _trace(state, "gate")}


def decide_payment(state: PaymentPipelineState) -> dict:
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    if not gate.allowed:
        decision = PaymentDecision(
            action="hold",
            reasoning=f"Decision held because the risk gate blocked the event: {gate.reason}",
        )
    else:
        action = ACTION_BY_CAUSE[diagnosis.cause]
        decision = PaymentDecision(
            action=action,
            reasoning=f"Cause {diagnosis.cause!r} maps to action {action!r}; {gate.reason}.",
        )
    return {"decision": decision, "stage_trace": _trace(state, "decide")}


def _recipient(event: Event, history: CustomerHistoryRow | None, channel: str) -> str | None:
    if channel == "whatsapp":
        return event.metadata.get("whatsapp_to") or event.metadata.get("phone")
    return event.metadata.get("sms_to") or event.metadata.get("phone")


def _execute_action(
    event: Event,
    history: CustomerHistoryRow | None,
    decision: PaymentDecision,
) -> tuple[SendResult | None, str]:
    if decision.action == "hold":
        return None, "blocked"
    if decision.action in {"retry_now", "retry_alt_route"}:
        return None, "mock_retry_recorded"

    channel = history.preferred_channel if history is not None else "sms"
    channel = channel if channel in {"sms", "whatsapp"} else "sms"
    recipient = _recipient(event, history, channel)
    if not recipient:
        return None, "not_sent_missing_recipient"

    values = {
        "customer_name": event.metadata.get("customer_name", event.customer_id),
        "amount": f"{event.amount:.2f} {event.currency}",
        "link": event.metadata.get(
            "payment_link",
            f"{settings.payment_link_base_url}/{event.event_id}",
        ),
    }
    template = PAYMENT_LINK_WHATSAPP if channel == "whatsapp" else PAYMENT_LINK_SMS
    rendered = render(template, **values)
    if channel == "whatsapp":
        delivery = send_whatsapp(recipient, rendered.body)
    else:
        delivery = send_sms(
            recipient,
            rendered.body,
            content_variables={"1": values["customer_name"], "2": values["amount"], "3": values["link"]},
        )
    return delivery, delivery.status


def _write_audit(
    db: Session,
    event: Event,
    diagnosis: PaymentDiagnosis,
    gate: GateDecision,
    decision: PaymentDecision,
    delivery_status: str,
) -> int:
    reasoning = (
        f"cause={diagnosis.cause}; diagnosis={diagnosis.reasoning}; "
        f"gate={gate.reason}; decision={decision.reasoning}; "
        f"delivery={delivery_status}"
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


def execute_and_log(state: PaymentPipelineState) -> dict:
    event = state["event"]
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    decision = state["decision"]
    delivery, delivery_status = _execute_action(event, state.get("customer_history"), decision)
    audit_log_id = _write_audit(
        state["db"],
        event,
        diagnosis,
        gate,
        decision,
        delivery_status,
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
        delivery_status=delivery_status,
    )
    return {
        "delivery": delivery,
        "delivery_status": delivery_status,
        "audit_log_id": audit_log_id,
        "stage_trace": trace,
        "response": response,
    }


def build_payment_pipeline():
    builder = StateGraph(PaymentPipelineState)
    builder.add_node("fetch", fetch_event)
    builder.add_node("enrich", enrich_context)
    builder.add_node("classify", classify_payment)
    builder.add_node("gate", gate_payment)
    builder.add_node("decide", decide_payment)
    builder.add_node("execute_log", execute_and_log)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "enrich")
    builder.add_edge("enrich", "classify")
    builder.add_edge("classify", "gate")
    builder.add_edge("gate", "decide")
    builder.add_edge("decide", "execute_log")
    builder.add_edge("execute_log", END)
    return builder.compile()


payment_pipeline = build_payment_pipeline()


def run_payment_event(event: Event, db: Session | None = None) -> dict:
    owns_session = db is None
    active_db = db or SessionLocal()
    try:
        return payment_pipeline.invoke({"event": event, "db": active_db})
    except Exception:
        active_db.rollback()
        raise
    finally:
        if owns_session:
            active_db.close()


def payment_agent(state: PipelineState) -> dict:
    result = run_payment_event(state["event"], state.get("db"))
    return {
        "response": result["response"],
        "payment_result": result["response"],
        "audit_log_id": result["audit_log_id"],
    }
