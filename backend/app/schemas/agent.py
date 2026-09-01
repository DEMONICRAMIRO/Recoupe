from pydantic import BaseModel, Field

PLACEHOLDER_ACTION = "placeholder_action"
STUB_REASONING = "stub agent - real diagnosis and decision logic arrives in Phase 2"
STUB_STATUS = "stub"


class SubAgentResponse(BaseModel):
    agent: str
    event_id: str
    event_type: str
    action: str = PLACEHOLDER_ACTION
    reasoning: str = STUB_REASONING
    status: str = STUB_STATUS
    cause: str | None = None
    gate_allowed: bool | None = None
    gate_reason: str | None = None
    stage_trace: list[str] = Field(default_factory=list)
    delivery_status: str | None = None
