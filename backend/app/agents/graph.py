from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.cart_agent import cart_agent
from app.agents.invoice_agent import invoice_agent, run_invoice_event
from app.agents.orchestrator import PipelineState, classify, route_to_agent
from app.agents.payment_agent import payment_agent
from app.agents.renewal_agent import renewal_agent
from app.schemas.agent import PLACEHOLDER_ACTION, STUB_REASONING, STUB_STATUS, SubAgentResponse
from app.schemas.event import Event

def payment_route_node(state: PipelineState) -> dict:
    result = payment_agent(state)
    event = state["event"]
    legacy_response = SubAgentResponse(
        agent="payment_agent",
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=PLACEHOLDER_ACTION,
        reasoning=STUB_REASONING,
        status=STUB_STATUS,
    )
    return {**result, "response": legacy_response, "payment_result": result["response"]}


def cart_route_node(state: PipelineState) -> dict:
    result = cart_agent(state)
    event = state["event"]
    legacy_response = SubAgentResponse(
        agent="cart_agent",
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=PLACEHOLDER_ACTION,
        reasoning=STUB_REASONING,
        status=STUB_STATUS,
    )
    return {**result, "response": legacy_response, "cart_result": result["response"]}


def renewal_route_node(state: PipelineState) -> dict:
    result = renewal_agent(state)
    event = state["event"]
    legacy_response = SubAgentResponse(
        agent="renewal_agent",
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=PLACEHOLDER_ACTION,
        reasoning=STUB_REASONING,
        status=STUB_STATUS,
    )
    return {**result, "response": legacy_response, "renewal_result": result["response"]}


def invoice_route_node(state: PipelineState) -> dict:
    result = invoice_agent(state)
    event = state["event"]
    legacy_response = SubAgentResponse(
        agent="invoice_agent",
        event_id=event.event_id,
        event_type=event.event_type.value,
        action=PLACEHOLDER_ACTION,
        reasoning=STUB_REASONING,
        status=STUB_STATUS,
    )
    return {**result, "response": legacy_response, "invoice_result": result["response"]}


NODE_FUNCTIONS = {
    "payment_agent": payment_route_node,
    "cart_agent": cart_route_node,
    "renewal_agent": renewal_route_node,
    "invoice_agent": invoice_route_node,
}


def build_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("classify", classify)
    for name, node_fn in NODE_FUNCTIONS.items():
        builder.add_node(name, node_fn)
        builder.add_edge(name, END)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify",
        route_to_agent,
        {name: name for name in NODE_FUNCTIONS},
    )
    return builder.compile()


pipeline = build_graph()


def run_event(event: Event, db: Session | None = None) -> dict:
    initial_state = {"event": event}
    if db is not None:
        initial_state["db"] = db
    return pipeline.invoke(initial_state)
