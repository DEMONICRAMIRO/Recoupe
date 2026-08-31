from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    payment_failed = "payment_failed"
    cart_abandoned = "cart_abandoned"
    renewal_failed = "renewal_failed"
    invoice_overdue = "invoice_overdue"


class Event(BaseModel):
    event_id: str
    event_type: EventType
    customer_id: str
    amount: float
    currency: str
    reason_code: str | None = None
    timestamp: datetime
    metadata: dict = Field(default_factory=dict)
