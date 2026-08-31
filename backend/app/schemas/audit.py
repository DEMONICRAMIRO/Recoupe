from datetime import datetime

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: int
    event_id: str
    action_taken: str
    reasoning: str
    timestamp: datetime
