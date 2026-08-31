import json
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.event import Event, EventType

OUTPUT_DIR = Path(__file__).resolve().parent

Faker.seed(42)
random.seed(42)
fake = Faker("en_IN")

EVENTS_PER_TYPE = 30
CUSTOMER_COUNT = 45

PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]
DECLINE_CODES = ["insufficient_funds", "card_expired", "auth_declined", "processor_error", "do_not_honor", "risk_blocked"]
RENEWAL_CODES = ["card_expired", "insufficient_funds", "processor_error", "mandate_revoked"]
CHANNELS = ["email", "sms", "whatsapp", "push"]
DEVICES = ["mobile", "desktop"]
PLANS = ["monthly", "yearly"]
TIERS = ["starter", "growth", "enterprise"]


def make_customers(count: int) -> list[dict]:
    return [
        {
            "customer_id": f"cus_{uuid.uuid4().hex[:12]}",
            "name": fake.name(),
            "preferred_channel": random.choice(CHANNELS),
        }
        for _ in range(count)
    ]


def customer_cycle(customers: list[dict]):
    while True:
        batch = customers[:]
        random.shuffle(batch)
        for customer in batch:
            yield customer


def random_timestamp(start: datetime, end: datetime) -> datetime:
    return start + (end - start) * random.random()


def make_payment_event(customer: dict, start: datetime, end: datetime) -> dict:
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": EventType.payment_failed.value,
        "customer_id": customer["customer_id"],
        "amount": round(random.uniform(99, 15000), 2),
        "currency": "INR",
        "reason_code": random.choice(DECLINE_CODES),
        "timestamp": random_timestamp(start, end).isoformat(),
        "metadata": {
            "customer_name": customer["name"],
            "payment_method": random.choice(PAYMENT_METHODS),
            "gateway": "razorpay",
            "attempt": random.randint(1, 3),
        },
    }


def make_cart_event(customer: dict, start: datetime, end: datetime) -> dict:
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": EventType.cart_abandoned.value,
        "customer_id": customer["customer_id"],
        "amount": round(random.uniform(199, 8000), 2),
        "currency": "INR",
        "reason_code": None,
        "timestamp": random_timestamp(start, end).isoformat(),
        "metadata": {
            "customer_name": customer["name"],
            "cart_items": random.randint(1, 6),
            "device": random.choice(DEVICES),
            "session_duration_sec": random.randint(30, 1800),
        },
    }


def make_renewal_event(customer: dict, start: datetime, end: datetime) -> dict:
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": EventType.renewal_failed.value,
        "customer_id": customer["customer_id"],
        "amount": round(random.uniform(99, 2000), 2),
        "currency": "INR",
        "reason_code": random.choice(RENEWAL_CODES),
        "timestamp": random_timestamp(start, end).isoformat(),
        "metadata": {
            "customer_name": customer["name"],
            "subscription_id": f"sub_{uuid.uuid4().hex[:10]}",
            "plan": random.choice(PLANS),
            "attempt": random.randint(1, 3),
        },
    }


def make_invoice_event(customer: dict, start: datetime, end: datetime, seq: int) -> dict:
    days_overdue = random.randint(1, 120)
    due_date = (end - timedelta(days=days_overdue)).date().isoformat()
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": EventType.invoice_overdue.value,
        "customer_id": customer["customer_id"],
        "amount": round(random.uniform(5000, 200000), 2),
        "currency": "INR",
        "reason_code": None,
        "timestamp": random_timestamp(start, end).isoformat(),
        "metadata": {
            "customer_name": customer["name"],
            "invoice_number": f"INV-2026-{seq:04d}",
            "due_date": due_date,
            "days_overdue": days_overdue,
            "account_tier": random.choice(TIERS),
        },
    }


def build_history(customers: list[dict], events: list[Event]) -> list[dict]:
    by_customer: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_customer[event.customer_id].append(event)

    now = datetime.now(timezone.utc)
    history = []
    for customer in customers:
        customer_events = by_customer.get(customer["customer_id"], [])
        latest = max(customer_events, key=lambda e: e.timestamp) if customer_events else None
        if random.random() < 0.3:
            last_contacted = None
        else:
            last_contacted = (now - timedelta(days=random.uniform(0, 14))).isoformat()
        history.append(
            {
                "customer_id": customer["customer_id"],
                "event_type": latest.event_type.value if latest else None,
                "last_contacted_at": last_contacted,
                "contact_count_7d": random.randint(0, 4),
                "past_failures": random.randint(0, 5),
                "preferred_channel": customer["preferred_channel"],
            }
        )
    return history


def main() -> None:
    customers = make_customers(CUSTOMER_COUNT)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)

    cycle = customer_cycle(customers)
    raw_events = []
    for _ in range(EVENTS_PER_TYPE):
        raw_events.append(make_payment_event(next(cycle), start, end))
    for _ in range(EVENTS_PER_TYPE):
        raw_events.append(make_cart_event(next(cycle), start, end))
    for _ in range(EVENTS_PER_TYPE):
        raw_events.append(make_renewal_event(next(cycle), start, end))
    for seq in range(EVENTS_PER_TYPE):
        raw_events.append(make_invoice_event(next(cycle), start, end, seq + 1))

    validated = [Event.model_validate(item) for item in raw_events]
    events_out = [event.model_dump(mode="json") for event in validated]
    history_out = build_history(customers, validated)

    (OUTPUT_DIR / "events.json").write_text(json.dumps(events_out, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "customer_history.json").write_text(json.dumps(history_out, indent=2), encoding="utf-8")

    counts: dict[str, int] = defaultdict(int)
    for event in events_out:
        counts[event["event_type"]] += 1
    print(f"Generated {len(events_out)} events across {CUSTOMER_COUNT} customers")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type}: {count}")
    print(f"Seeded {len(history_out)} customer_history rows")


if __name__ == "__main__":
    main()
