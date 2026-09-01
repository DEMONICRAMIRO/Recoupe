from typing import TypedDict

from sqlalchemy.orm import Session

from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType

ROUTE_TABLE = {
    EventType.payment_failed: "payment_agent",
    EventType.cart_abandoned: "cart_agent",
    EventType.renewal_failed: "renewal_agent",
    EventType.invoice_overdue: "invoice_agent",
}


class PipelineState(TypedDict, total=False):
    event: Event
    db: Session
    routed_to: str
    response: SubAgentResponse
    payment_result: SubAgentResponse
    audit_log_id: int


def classify(state: PipelineState) -> dict:
    event = state["event"]
    return {"routed_to": ROUTE_TABLE[event.event_type]}


def route_to_agent(state: PipelineState) -> str:
    return state["routed_to"]
