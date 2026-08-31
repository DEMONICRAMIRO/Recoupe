from datetime import datetime

from pydantic import BaseModel


class CustomerHistory(BaseModel):
    customer_id: str
    event_type: str | None = None
    last_contacted_at: datetime | None = None
    contact_count_7d: int = 0
    past_failures: int = 0
    preferred_channel: str
