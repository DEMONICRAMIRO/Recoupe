from fastapi import APIRouter

from app.agents.graph import run_event
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/ingest", response_model=SubAgentResponse)
def ingest_event(event: Event) -> SubAgentResponse:
    state = run_event(event)
    return state["response"]
