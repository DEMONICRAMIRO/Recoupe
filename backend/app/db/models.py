from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    customer_id = Column(String, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    reason_code = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    event_metadata = Column("metadata", JSON, nullable=False, default=dict)


class CustomerHistoryRow(Base):
    __tablename__ = "customer_history"

    customer_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=True)
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    contact_count_7d = Column(Integer, nullable=False, default=0)
    past_failures = Column(Integer, nullable=False, default=0)
    preferred_channel = Column(String, nullable=False)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, nullable=False, index=True)
    action_taken = Column(String, nullable=False)
    reasoning = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=utc_now)
