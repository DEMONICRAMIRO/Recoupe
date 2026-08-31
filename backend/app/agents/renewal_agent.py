import logging

from app.agents.orchestrator import PipelineState
from app.schemas.agent import SubAgentResponse

logger = logging.getLogger(__name__)

AGENT_NAME = "renewal_agent"


def renewal_agent(state: PipelineState) -> dict:
    event = state["event"]
    logger.info("%s received event %s (%s)", AGENT_NAME, event.event_id, event.event_type.value)
    response = SubAgentResponse(
        agent=AGENT_NAME,
        event_id=event.event_id,
        event_type=event.event_type.value,
    )
    return {"response": response}
