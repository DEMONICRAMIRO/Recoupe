"""
generate_persona.py — Typed customer persona dataset.

Generates 8 customer archetypes, each with hand-crafted attributes that
deterministically exercise a specific pipeline branch. Unlike the random
generate.py, every field here is intentional — the persona name tells you
exactly what outcome to expect.

Archetypes
----------
P1  Priya_Champion      payment_failed / insufficient_funds
    → Gate: allowed  |  Action: retry_alt_route
P2  Rajan_Cooldown      payment_failed / expired_card
    → Gate: blocked (cooldown active — contacted 2 hrs ago)
P3  Dev_MaxRetries      payment_failed / auth_declined
    → Gate: blocked (contact_count_7d=3 == max)
P4  Aisha_PayLink       payment_failed / card_expired
    → Gate: allowed  |  Action: send_payment_link (expired_card cause)
P5  Meera_CartNudge     cart_abandoned (first-time buyer, high-value cart)
    → Gate: allowed  |  Action: send_nudge
P6  Sanjay_CartBlocked  cart_abandoned
    → Gate: blocked (contact_count_7d=3)
P7  Anita_Renewal       renewal_failed / card_expired
    → Gate: allowed  |  Action: send_card_update_link
P8  Vikram_MandateGrace renewal_failed / mandate_revoked
    → Gate: blocked (cooldown — contacted 1 hour ago)
P9  Suresh_GoodRenewal  renewal_failed / insufficient_funds, recent but outside cooldown
    → Gate: allowed  |  Action: send_payment_link
P10 Nisha_InvoiceCall   invoice_overdue — 100d, poor reliability, broken promise
    → Gate: allowed  |  Call priority: CALL, score=1.0 (pure rule)
P11 Kavitha_InvoiceEmail invoice_overdue — 15d, good reliability, no broken promise
    → Gate: allowed  |  Call priority: EMAIL, score=0.0 (pure rule)
P12 Amit_InvoiceBlocked  invoice_overdue — escalation_count=2 (== max)
    → Invoice gate: blocked
P13 Leela_InvoiceSMS     invoice_overdue — 95d, good reliability, no broken promise
    → Gate: allowed  |  Call priority: SMS, score=0.5 (rule)
P14 Rohan_InvoiceAmbig   invoice_overdue — 55d, fair reliability, broken promise
    → Gate: allowed  |  Call priority: CALL or SMS (LLM or heuristic, non-deterministic)
"""

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.event import Event, EventType

OUTPUT_DIR = Path(__file__).resolve().parent
NOW = datetime.now(timezone.utc)

# ─── Contact phone / email for real comms dispatch in persona tests ───────────
REAL_PHONE = "+919631581658"
REAL_EMAIL = "arjunkumarsingh166@gmail.com"


def _eid() -> str:
    return f"persona_{uuid.uuid4().hex[:10]}"


# ─── PERSONAS ─────────────────────────────────────────────────────────────────

PERSONAS: list[dict] = [
    # P1 — Champion: never contacted, insufficient_funds → retry_alt_route
    {
        "persona": "P1_Priya_Champion",
        "expected_gate": "allowed",
        "expected_action": "retry_alt_route",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.payment_failed.value,
            "customer_id": "persona_priya_champion",
            "amount": 4999.00,
            "currency": "INR",
            "reason_code": "insufficient_funds",
            "timestamp": (NOW - timedelta(minutes=5)).isoformat(),
            "metadata": {
                "customer_name": "Priya Ramachandran",
                "payment_method": "card",
                "gateway": "razorpay",
                "attempt": 1,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_priya_champion",
            "event_type": EventType.payment_failed.value,
            "last_contacted_at": None,          # Never contacted → no cooldown
            "contact_count_7d": 0,
            "past_failures": 1,
            "preferred_channel": "email",
        },
    },

    # P2 — Cooldown: contacted 2 hrs ago, card expired → gate blocks
    {
        "persona": "P2_Rajan_Cooldown",
        "expected_gate": "blocked",
        "expected_action": "hold",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.payment_failed.value,
            "customer_id": "persona_rajan_cooldown",
            "amount": 2500.00,
            "currency": "INR",
            "reason_code": "card_expired",
            "timestamp": (NOW - timedelta(minutes=3)).isoformat(),
            "metadata": {
                "customer_name": "Rajan Mehta",
                "payment_method": "card",
                "gateway": "razorpay",
                "attempt": 2,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_rajan_cooldown",
            "event_type": EventType.payment_failed.value,
            "last_contacted_at": (NOW - timedelta(hours=2)).isoformat(),  # within 24hr cooldown
            "contact_count_7d": 1,
            "past_failures": 3,
            "preferred_channel": "sms",
        },
    },

    # P3 — MaxRetries: contact_count_7d=3 == risk_max_retries → gate blocks
    {
        "persona": "P3_Dev_MaxRetries",
        "expected_gate": "blocked",
        "expected_action": "hold",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.payment_failed.value,
            "customer_id": "persona_dev_maxretries",
            "amount": 799.00,
            "currency": "INR",
            "reason_code": "auth_declined",
            "timestamp": (NOW - timedelta(minutes=10)).isoformat(),
            "metadata": {
                "customer_name": "Dev Sharma",
                "payment_method": "upi",
                "gateway": "razorpay",
                "attempt": 3,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_dev_maxretries",
            "event_type": EventType.payment_failed.value,
            "last_contacted_at": (NOW - timedelta(days=2)).isoformat(),  # cooldown OK
            "contact_count_7d": 3,              # == risk_max_retries → blocked
            "past_failures": 5,
            "preferred_channel": "sms",
        },
    },

    # P4 — PayLink: expired card, fresh account → send_payment_link
    {
        "persona": "P4_Aisha_PayLink",
        "expected_gate": "allowed",
        "expected_action": "send_payment_link",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.payment_failed.value,
            "customer_id": "persona_aisha_paylink",
            "amount": 8999.00,
            "currency": "INR",
            "reason_code": "card_expired",
            "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
            "metadata": {
                "customer_name": "Aisha Khan",
                "payment_method": "card",
                "gateway": "razorpay",
                "attempt": 1,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_aisha_paylink",
            "event_type": EventType.payment_failed.value,
            "last_contacted_at": (NOW - timedelta(days=5)).isoformat(),
            "contact_count_7d": 0,
            "past_failures": 0,
            "preferred_channel": "email",
        },
    },

    # P5 — CartNudge: high-value first-time cart, no prior contact → send_nudge
    {
        "persona": "P5_Meera_CartNudge",
        "expected_gate": "allowed",
        "expected_action": "send_nudge",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.cart_abandoned.value,
            "customer_id": "persona_meera_cartnudge",
            "amount": 6499.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(hours=1)).isoformat(),
            "metadata": {
                "customer_name": "Meera Nair",
                "cart_items": 3,
                "device": "mobile",
                "session_duration_sec": 320,
                "purchase_history_count": 0,    # First-time buyer
                "avg_order_value": 6499.00,
                "price_sensitivity": "low",     # High-value shopper
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_meera_cartnudge",
            "event_type": None,
            "last_contacted_at": None,
            "contact_count_7d": 0,
            "past_failures": 0,
            "preferred_channel": "email",
        },
    },

    # P6 — CartBlocked: contact maxed out → gate blocks
    {
        "persona": "P6_Sanjay_CartBlocked",
        "expected_gate": "blocked",
        "expected_action": "hold",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.cart_abandoned.value,
            "customer_id": "persona_sanjay_cartblocked",
            "amount": 1299.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(hours=2)).isoformat(),
            "metadata": {
                "customer_name": "Sanjay Patel",
                "cart_items": 1,
                "device": "desktop",
                "session_duration_sec": 45,
                "purchase_history_count": 8,
                "avg_order_value": 900.00,
                "price_sensitivity": "high",
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_sanjay_cartblocked",
            "event_type": EventType.cart_abandoned.value,
            "last_contacted_at": (NOW - timedelta(days=3)).isoformat(),
            "contact_count_7d": 3,              # == risk_max_retries
            "past_failures": 2,
            "preferred_channel": "sms",
        },
    },

    # P7 — RenewalCardUpdate: card_expired, 36-month tenure → send_card_update_link
    {
        "persona": "P7_Anita_RenewalCardUpdate",
        "expected_gate": "allowed",
        "expected_action": "send_card_update_link",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.renewal_failed.value,
            "customer_id": "persona_anita_renewal",
            "amount": 999.00,
            "currency": "INR",
            "reason_code": "card_expired",
            "timestamp": (NOW - timedelta(hours=3)).isoformat(),
            "metadata": {
                "customer_name": "Anita Desai",
                "subscription_id": "sub_persona_anita",
                "plan": "yearly",
                "attempt": 1,
                "tenure_months": 36,
                "ltv_amount": 35964.00,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_anita_renewal",
            "event_type": EventType.renewal_failed.value,
            "last_contacted_at": (NOW - timedelta(days=30)).isoformat(),
            "contact_count_7d": 0,
            "past_failures": 0,
            "preferred_channel": "email",
        },
    },

    # P8 — MandateBlocked: contacted 1 hour ago (inside cooldown) → gate blocks
    {
        "persona": "P8_Vikram_MandateBlocked",
        "expected_gate": "blocked",
        "expected_action": "hold",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.renewal_failed.value,
            "customer_id": "persona_vikram_mandate",
            "amount": 499.00,
            "currency": "INR",
            "reason_code": "mandate_revoked",
            "timestamp": (NOW - timedelta(minutes=30)).isoformat(),
            "metadata": {
                "customer_name": "Vikram Singh",
                "subscription_id": "sub_persona_vikram",
                "plan": "monthly",
                "attempt": 2,
                "tenure_months": 6,
                "ltv_amount": 2994.00,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_vikram_mandate",
            "event_type": EventType.renewal_failed.value,
            "last_contacted_at": (NOW - timedelta(hours=1)).isoformat(),  # inside 24hr cooldown
            "contact_count_7d": 1,
            "past_failures": 1,
            "preferred_channel": "sms",
        },
    },

    # P9 — GoodRenewal: insufficient_funds, outside cooldown → allowed → send_payment_link
    {
        "persona": "P9_Suresh_GoodRenewal",
        "expected_gate": "allowed",
        "expected_action": "send_payment_link",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.renewal_failed.value,
            "customer_id": "persona_suresh_goodrenewal",
            "amount": 1499.00,
            "currency": "INR",
            "reason_code": "insufficient_funds",
            "timestamp": (NOW - timedelta(hours=6)).isoformat(),
            "metadata": {
                "customer_name": "Suresh Kumar",
                "subscription_id": "sub_persona_suresh",
                "plan": "monthly",
                "attempt": 1,
                "tenure_months": 18,
                "ltv_amount": 26982.00,
                "email": REAL_EMAIL,
                "phone": REAL_PHONE,
            },
        },
        "history": {
            "customer_id": "persona_suresh_goodrenewal",
            "event_type": EventType.renewal_failed.value,
            "last_contacted_at": (NOW - timedelta(hours=30)).isoformat(),  # outside cooldown
            "contact_count_7d": 1,
            "past_failures": 2,
            "preferred_channel": "email",
        },
    },

    # P10 — DeadbeatInvoice: 100d, poor, broken_promise → CALL tier (rule: score=1.0)
    {
        "persona": "P10_Nisha_InvoiceCall",
        "expected_gate": "allowed",
        "expected_call_tier": "call",
        "expected_call_score_min": 0.9,
        "event": {
            "event_id": _eid(),
            "event_type": EventType.invoice_overdue.value,
            "customer_id": "persona_nisha_invoicecall",
            "amount": 125000.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(days=100)).isoformat(),
            "metadata": {
                "customer_name": "Nisha Verma",
                "invoice_number": "INV-PERSONA-0010",
                "due_date": (NOW - timedelta(days=100)).date().isoformat(),
                "days_overdue": 100,
                "account_tier": "enterprise",
                "account_reliability": "poor",
                "broken_promise": True,
                "phone": REAL_PHONE,
                "email": REAL_EMAIL,
            },
        },
        "history": {
            "customer_id": "persona_nisha_invoicecall",
            "event_type": EventType.invoice_overdue.value,
            "last_contacted_at": None,
            "contact_count_7d": 0,
            "past_failures": 4,
            "preferred_channel": "email",
            "invoice_escalation_count_30d": 0,
            "last_invoice_contact_at": None,
        },
    },

    # P11 — FreshEnterprise: 15d, good, no broken promise → EMAIL tier (rule: score=0.0)
    {
        "persona": "P11_Kavitha_InvoiceEmail",
        "expected_gate": "allowed",
        "expected_call_tier": "email",
        "expected_call_score_max": 0.15,
        "event": {
            "event_id": _eid(),
            "event_type": EventType.invoice_overdue.value,
            "customer_id": "persona_kavitha_invoiceemail",
            "amount": 18000.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(days=15)).isoformat(),
            "metadata": {
                "customer_name": "Kavitha Menon",
                "invoice_number": "INV-PERSONA-0011",
                "due_date": (NOW - timedelta(days=15)).date().isoformat(),
                "days_overdue": 15,
                "account_tier": "growth",
                "account_reliability": "good",
                "broken_promise": False,
                "phone": REAL_PHONE,
                "email": REAL_EMAIL,
            },
        },
        "history": {
            "customer_id": "persona_kavitha_invoiceemail",
            "event_type": EventType.invoice_overdue.value,
            "last_contacted_at": None,
            "contact_count_7d": 0,
            "past_failures": 0,
            "preferred_channel": "email",
        },
    },

    # P12 — InvoiceBlocked: escalation_count=2 == max → invoice gate blocks
    {
        "persona": "P12_Amit_InvoiceGateBlocked",
        "expected_gate": "blocked",
        "expected_action": "hold",
        "event": {
            "event_id": _eid(),
            "event_type": EventType.invoice_overdue.value,
            "customer_id": "persona_amit_invblocked",
            "amount": 55000.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(days=45)).isoformat(),
            "metadata": {
                "customer_name": "Amit Joshi",
                "invoice_number": "INV-PERSONA-0012",
                "due_date": (NOW - timedelta(days=45)).date().isoformat(),
                "days_overdue": 45,
                "account_tier": "starter",
                "account_reliability": "fair",
                "broken_promise": False,
                "phone": REAL_PHONE,
                "email": REAL_EMAIL,
            },
        },
        "history": {
            "customer_id": "persona_amit_invblocked",
            "event_type": EventType.invoice_overdue.value,
            "last_contacted_at": (NOW - timedelta(days=5)).isoformat(),
            "contact_count_7d": 3,
            "past_failures": 2,
            "preferred_channel": "email",
        },
    },

    # P13 — InvoiceSMS: 95d, good reliability, no broken promise → SMS (rule: score=0.5)
    {
        "persona": "P13_Leela_InvoiceSMS",
        "expected_gate": "allowed",
        "expected_call_tier": "sms",
        "expected_call_score_min": 0.4,
        "expected_call_score_max": 0.6,
        "event": {
            "event_id": _eid(),
            "event_type": EventType.invoice_overdue.value,
            "customer_id": "persona_leela_invoicesms",
            "amount": 32000.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(days=95)).isoformat(),
            "metadata": {
                "customer_name": "Leela Iyer",
                "invoice_number": "INV-PERSONA-0013",
                "due_date": (NOW - timedelta(days=95)).date().isoformat(),
                "days_overdue": 95,
                "account_tier": "growth",
                "account_reliability": "good",
                "broken_promise": False,
                "phone": REAL_PHONE,
                "email": REAL_EMAIL,
            },
        },
        "history": {
            "customer_id": "persona_leela_invoicesms",
            "event_type": EventType.invoice_overdue.value,
            "last_contacted_at": (NOW - timedelta(days=10)).isoformat(),
            "contact_count_7d": 0,
            "past_failures": 0,
            "preferred_channel": "sms",
            "invoice_escalation_count_30d": 0,
            "last_invoice_contact_at": None,
        },
    },

    # P14 — InvoiceAmbig: 55d, fair, broken_promise — LLM/heuristic case
    {
        "persona": "P14_Rohan_InvoiceAmbig",
        "expected_gate": "allowed",
        "expected_call_tier_options": ["call", "sms"],  # non-deterministic: LLM or heuristic
        "event": {
            "event_id": _eid(),
            "event_type": EventType.invoice_overdue.value,
            "customer_id": "persona_rohan_invoiceambig",
            "amount": 47500.00,
            "currency": "INR",
            "reason_code": None,
            "timestamp": (NOW - timedelta(days=55)).isoformat(),
            "metadata": {
                "customer_name": "Rohan Gupta",
                "invoice_number": "INV-PERSONA-0014",
                "due_date": (NOW - timedelta(days=55)).date().isoformat(),
                "days_overdue": 55,
                "account_tier": "enterprise",
                "account_reliability": "fair",
                "broken_promise": True,
                "phone": REAL_PHONE,
                "email": REAL_EMAIL,
            },
        },
        "history": {
            "customer_id": "persona_rohan_invoiceambig",
            "event_type": EventType.invoice_overdue.value,
            "last_contacted_at": (NOW - timedelta(days=7)).isoformat(),
            "contact_count_7d": 0,
            "past_failures": 1,
            "preferred_channel": "email",
            "invoice_escalation_count_30d": 0,
            "last_invoice_contact_at": None,
        },
    },
]


def main() -> None:
    events_out = []
    history_out = []

    for p in PERSONAS:
        event_raw = p["event"]
        validated = Event.model_validate(event_raw)
        events_out.append({
            "persona": p["persona"],
            **validated.model_dump(mode="json"),
        })
        history_out.append(p["history"])

    (OUTPUT_DIR / "persona_events.json").write_text(
        json.dumps(events_out, indent=2, default=str), encoding="utf-8"
    )
    (OUTPUT_DIR / "persona_history.json").write_text(
        json.dumps(history_out, indent=2, default=str), encoding="utf-8"
    )

    print(f"Generated {len(events_out)} persona events -> persona_events.json")
    print(f"Generated {len(history_out)} customer histories -> persona_history.json")
    for p in PERSONAS:
        eg = p.get("expected_gate", "?")
        ea = p.get("expected_action") or p.get("expected_call_tier") or p.get("expected_call_tier_options", "?")
        print(f"  {p['persona']:<35}  gate={eg}  outcome={ea}")


if __name__ == "__main__":
    main()
