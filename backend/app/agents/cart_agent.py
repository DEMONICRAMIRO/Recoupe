import json
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
from app.core.llm_client import LLMUnavailable, ModelMessage, chat
from app.core.risk_gate import GateDecision, evaluate, evaluate_call
from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow, utc_now
from app.db.session import SessionLocal
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType
from app.services.comms import SendResult
from app.services.comms.email import send_email
from app.services.comms.feedback import parse_feedback_reply, record_feedback, send_feedback_prompt
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.cart_nudge import (
    CART_DISCOUNT_EMAIL,
    CART_DISCOUNT_SMS,
    CART_NUDGE_EMAIL,
    CART_NUDGE_SMS,
)
from app.services.comms.templates.cart_voice import VOICE_CART_HINGLISH
from app.services.comms.voice import place_voice_call

logger = logging.getLogger(__name__)

AGENT_NAME = "cart_agent"
CART_STAGES = ("fetch", "enrich", "diagnose", "gate", "decide", "execute_log")

CART_REASONS = ("price", "timeout", "distraction")
DISCOUNT_TIERS = {"medium": "5%", "high": "10%", "low": "5%"}

DIAGNOSE_PROMPT = (
    "You are diagnosing why a customer abandoned their shopping cart. "
    "Signals: cart_amount={cart_amount}, cart_items={cart_items}, device={device}, "
    "session_duration_sec={session_duration_sec}, purchase_history_count={purchase_history_count}, "
    "avg_order_value={avg_order_value}, price_sensitivity={price_sensitivity}, "
    "contact_count_7d={contact_count_7d}, past_failures={past_failures}.\n"
    "Classify the single most likely reason as one of: price, timeout, distraction. "
    "Respond with ONLY JSON, no markdown fences: "
    '{{"reason": "<price|timeout|distraction>", "confidence": <0.0-1.0>, "explanation": "<one sentence>"}}'
)


@dataclass(frozen=True)
class CartDiagnosis:
    reason: str
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class CartDecision:
    nudge_type: str
    discount_tier: str | None
    reasoning: str


class CartPipelineState(TypedDict, total=False):
    event: Event
    db: Session
    customer_history: CustomerHistoryRow | None
    diagnosis: CartDiagnosis
    gate: GateDecision
    decision: CartDecision
    deliveries: list[dict]
    feedback_result: SendResult | None
    response: SubAgentResponse
    stage_trace: list[str]
    audit_log_id: int


def _trace(state: CartPipelineState, stage: str) -> list[str]:
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


def fetch_event(state: CartPipelineState) -> dict:
    event = state["event"]
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "fetch", agent=AGENT_NAME)
    if event.event_type is not EventType.cart_abandoned:
        raise ValueError("Cart Agent accepts only cart_abandoned events")
    _persist_event(state["db"], event)
    return {"stage_trace": _trace(state, "fetch")}


def enrich_context(state: CartPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "enrich", agent=AGENT_NAME)
    history = state["db"].get(CustomerHistoryRow, state["event"].customer_id)
    return {"customer_history": history, "stage_trace": _trace(state, "enrich")}


def _llm_diagnosis(event: Event, history: CustomerHistoryRow | None) -> CartDiagnosis | None:
    meta = event.metadata
    prompt = DIAGNOSE_PROMPT.format(
        cart_amount=event.amount,
        cart_items=meta.get("cart_items", 1),
        device=meta.get("device", "unknown"),
        session_duration_sec=meta.get("session_duration_sec", 0),
        purchase_history_count=meta.get("purchase_history_count", 0),
        avg_order_value=meta.get("avg_order_value", 0),
        price_sensitivity=meta.get("price_sensitivity", "low"),
        contact_count_7d=history.contact_count_7d if history else 0,
        past_failures=history.past_failures if history else 0,
    )
    try:
        message: ModelMessage = chat(prompt, max_tokens=200)
    except LLMUnavailable as exc:
        logger.info("Cart diagnosis LLM unavailable, using heuristic: %s", exc)
        return None

    text = (message.content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        reason = str(data.get("reason", "")).strip().lower()
        if reason not in CART_REASONS:
            return None
        confidence = float(data.get("confidence", 0.5))
        return CartDiagnosis(
            reason=reason,
            confidence=confidence,
            reasoning=(
                f"LLM scored reason={reason} confidence={confidence:.2f}: "
                f"{data.get('explanation', 'no explanation provided')}"
            ),
        )
    except (ValueError, TypeError, AttributeError):
        logger.warning("Cart LLM returned unparsable content, using heuristic")
        return None


def _heuristic_diagnosis(event: Event, history: CustomerHistoryRow | None) -> CartDiagnosis:
    meta = event.metadata
    amount = event.amount
    avg = float(meta.get("avg_order_value") or 0)
    sensitivity = str(meta.get("price_sensitivity", "low")).lower()
    session = int(meta.get("session_duration_sec") or 0)
    if avg and amount > 1.5 * avg and sensitivity in {"medium", "high"}:
        return CartDiagnosis(
            reason="price",
            confidence=0.7,
            reasoning=(
                f"Heuristic fallback: cart value {amount} far exceeds average order {avg} "
                f"and sensitivity is {sensitivity}"
            ),
        )
    if session < 60:
        return CartDiagnosis(
            reason="distraction",
            confidence=0.6,
            reasoning=f"Heuristic fallback: very short session ({session}s) suggests distraction",
        )
    return CartDiagnosis(
        reason="timeout",
        confidence=0.6,
        reasoning=f"Heuristic fallback: session of {session}s ended without purchase",
    )


def diagnose_cart(state: CartPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "classify", agent=AGENT_NAME)
    event = state["event"]
    history = state.get("customer_history")
    diagnosis = _llm_diagnosis(event, history) or _heuristic_diagnosis(event, history)
    return {"diagnosis": diagnosis, "stage_trace": _trace(state, "diagnose")}


def gate_cart(state: CartPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "gate", agent=AGENT_NAME)
    gate = evaluate(state.get("customer_history"))
    return {"gate": gate, "stage_trace": _trace(state, "gate")}


def decide_cart(state: CartPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "decide", agent=AGENT_NAME)
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    if not gate.allowed:
        decision = CartDecision(
            nudge_type="hold",
            discount_tier=None,
            reasoning=f"Decision held because the risk gate blocked the event: {gate.reason}",
        )
    elif diagnosis.reason == "price":
        sensitivity = str(state["event"].metadata.get("price_sensitivity", "low")).lower()
        tier = DISCOUNT_TIERS.get(sensitivity, "5%")
        decision = CartDecision(
            nudge_type="discount",
            discount_tier=tier,
            reasoning=f"Price-sensitive abandonment; discount nudge at {tier}",
        )
    else:
        decision = CartDecision(
            nudge_type="plain",
            discount_tier=None,
            reasoning=f"Reason {diagnosis.reason!r} gets a plain reminder nudge",
        )
    return {"decision": decision, "stage_trace": _trace(state, "decide")}


def _dispatch_nudge(
    event: Event,
    decision: CartDecision,
) -> tuple[list[dict], SendResult | None]:
    deliveries: list[dict] = []
    feedback_result: SendResult | None = None
    if decision.nudge_type == "hold":
        deliveries.append({"channel": "blocked", "status": "hold_no_dispatch"})
        return deliveries, feedback_result

    phone = event.metadata.get("phone")
    email = event.metadata.get("email")
    customer_name = event.metadata.get("customer_name", event.customer_id)
    values = {
        "customer_name": customer_name,
        "cart_items": event.metadata.get("cart_items", 1),
        "discount": decision.discount_tier or "5%",
        "link": event.metadata.get("payment_link", f"{settings.payment_link_base_url}/{event.event_id}"),
    }

    if decision.nudge_type == "discount":
        sms_template, email_template = CART_DISCOUNT_SMS, CART_DISCOUNT_EMAIL
    else:
        sms_template, email_template = CART_NUDGE_SMS, CART_NUDGE_EMAIL

    if phone:
        sms = send_sms(phone, render(sms_template, **values).body)
        if not sms.success:
            logger.error("cart_agent: sms delivery failed for event %s: %s", event.event_id, sms.error)
        deliveries.append({"channel": "sms", "result": sms.as_dict()})
        feedback_result = send_feedback_prompt(phone, customer_name)
    else:
        deliveries.append({"channel": "sms", "status": "not_sent_missing_recipient"})

    if email:
        rendered_email = render(email_template, **values)
        email_result = send_email(email, rendered_email.subject or "Recoupe", rendered_email.body)
        if not email_result.success:
            logger.error("cart_agent: email delivery failed for event %s: %s", event.event_id, email_result.error)
        deliveries.append({"channel": "email", "result": email_result.as_dict()})
    else:
        deliveries.append({"channel": "email", "status": "not_sent_missing_recipient"})

    return deliveries, feedback_result


def _delivery_str(item: dict) -> str:
    result = item.get("result")
    if result:
        error = result.get("error")
        return f"{result.get('status', 'unknown')}({error})" if error else result.get("status", "unknown")
    return item.get("status", "unknown")


def _write_audit(
    db: Session,
    event: Event,
    diagnosis: CartDiagnosis,
    gate: GateDecision,
    decision: CartDecision,
    deliveries: list[dict],
    feedback_sent: bool,
) -> int:
    delivery_summary = "; ".join(
        _delivery_str(item) for item in deliveries
    )
    reasoning = (
        f"diagnosis={diagnosis.reasoning}; gate={gate.reason}; "
        f"decision={decision.reasoning}; deliveries={delivery_summary}; "
        f"feedback_prompt={feedback_sent}"
    )
    row = db.execute(
        select(AuditLogRow)
        .where(AuditLogRow.event_id == event.event_id)
        .order_by(AuditLogRow.id.desc())
    ).scalars().first()
    action_taken = decision.nudge_type
    if decision.discount_tier:
        action_taken = f"{action_taken}_{decision.discount_tier}"
    if row is None:
        row = AuditLogRow(
            event_id=event.event_id,
            action_taken=action_taken,
            reasoning=reasoning,
            timestamp=utc_now(),
        )
        db.add(row)
    else:
        row.action_taken = action_taken
        row.reasoning = reasoning
        row.timestamp = utc_now()
    db.flush()
    db.commit()
    return row.id


def execute_and_log(state: CartPipelineState) -> dict:
    if (_ev := state.get("event")) is not None: update_live_status(_ev.event_id, "execute_log", agent=AGENT_NAME)
    event = state["event"]
    diagnosis = state["diagnosis"]
    gate = state["gate"]
    decision = state["decision"]
    deliveries, feedback_result = _dispatch_nudge(event, decision)
    audit_log_id = _write_audit(
        state["db"],
        event,
        diagnosis,
        gate,
        decision,
        deliveries,
        feedback_result is not None,
    )
    trace = _trace(state, "execute_log")
    response = SubAgentResponse(
        agent=AGENT_NAME,
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=decision.nudge_type,
        reasoning=f"{diagnosis.reasoning} {decision.reasoning}",
        status="processed",
        cause=diagnosis.reason,
        gate_allowed=gate.allowed,
        gate_reason=gate.reason,
        stage_trace=trace,
        delivery_status="; ".join(
            _delivery_str(item) for item in deliveries
        ),
        feedback_prompt_sent=feedback_result is not None,
    )
    return {
        "deliveries": deliveries,
        "feedback_result": feedback_result,
        "audit_log_id": audit_log_id,
        "stage_trace": trace,
        "response": response,
    }


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


def on_feedback_received(
    event_id: str,
    raw_reply: str,
    db: Session,
    *,
    voice_fn=None,
) -> dict:
    voice_sender = voice_fn or place_voice_call
    choice = parse_feedback_reply(raw_reply)
    record_feedback(db, event_id, choice.canonical)

    event_row = db.get(EventRow, event_id)
    if event_row is None:
        return {"placed": False, "reason": choice.canonical, "gate": None}

    if choice.canonical != "Payment issue":
        return {"placed": False, "reason": choice.canonical, "gate": None}

    phone = (event_row.event_metadata or {}).get("phone")
    customer_name = (event_row.event_metadata or {}).get("customer_name", event_row.customer_id)
    call_info = _call_history(db, event_row.customer_id)
    gate_call = evaluate_call(call_info)

    if not gate_call.allowed or not phone:
        return {
            "placed": False,
            "reason": choice.canonical,
            "gate": {"allowed": gate_call.allowed, "reason": gate_call.reason},
        }

    script = render(VOICE_CART_HINGLISH, customer_name=customer_name).body
    call_result = voice_sender(phone, script, url_params={"Name": customer_name})
    db.add(
        AuditLogRow(
            event_id=event_id,
            action_taken="voice_call",
            reasoning=(
                f"feedback_reason={choice.canonical}; call_gate={gate_call.reason}; "
                f"delivery={call_result.status}"
            ),
            timestamp=utc_now(),
        )
    )
    db.commit()
    return {
        "placed": True,
        "reason": choice.canonical,
        "gate": {"allowed": gate_call.allowed, "reason": gate_call.reason},
        "call": call_result.as_dict(),
    }


def build_cart_pipeline():
    builder = StateGraph(CartPipelineState)
    builder.add_node("fetch", fetch_event)
    builder.add_node("enrich", enrich_context)
    builder.add_node("diagnose", diagnose_cart)
    builder.add_node("gate", gate_cart)
    builder.add_node("decide", decide_cart)
    builder.add_node("execute_log", execute_and_log)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "enrich")
    builder.add_edge("enrich", "diagnose")
    builder.add_edge("diagnose", "gate")
    builder.add_edge("gate", "decide")
    builder.add_edge("decide", "execute_log")
    builder.add_edge("execute_log", END)
    return builder.compile()


cart_pipeline = build_cart_pipeline()


def run_cart_event(event: Event, db: Session | None = None) -> dict:
    owns_session = db is None
    active_db = db or SessionLocal()
    try:
        return cart_pipeline.invoke({"event": event, "db": active_db})
    except Exception:
        active_db.rollback()
        raise
    finally:
        if owns_session:
            active_db.close()


def cart_agent(state: PipelineState) -> dict:
    result = run_cart_event(state["event"], state.get("db"))
    return {
        "response": result["response"],
        "cart_result": result["response"],
        "audit_log_id": result["audit_log_id"],
    }
