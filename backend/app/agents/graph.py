from langgraph.graph import END, START, StateGraph

from app.agents.cart_agent import cart_agent
from app.agents.invoice_agent import invoice_agent
from app.agents.orchestrator import PipelineState, classify, route_to_agent
from app.agents.payment_agent import payment_agent
from app.agents.renewal_agent import renewal_agent
from app.schemas.event import Event

NODE_FUNCTIONS = {
    "payment_agent": payment_agent,
    "cart_agent": cart_agent,
    "renewal_agent": renewal_agent,
    "invoice_agent": invoice_agent,
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


def run_event(event: Event) -> dict:
    return pipeline.invoke({"event": event})
