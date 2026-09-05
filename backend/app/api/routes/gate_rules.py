"""gate_rules.py — Static endpoint reflecting the hardcoded risk gate constants.

These values are deterministic configuration constants in risk_gate.py / config.py.
They don't change per-request and don't need DB-backed configurability for the hackathon.
"""
from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(prefix="/gate-rules", tags=["gate-rules"])


@router.get("/")
def gate_rules() -> dict:
    """Return the deterministic risk gate constants used by all four agents."""
    return {
        "deterministic": True,
        "description": "All gate checks are pure if/else code. No LLM call ever routes through this layer.",
        "agents": {
            "payment_agent": {
                "max_retries": settings.risk_max_retries,
                "cooldown_hours": settings.risk_cooldown_hours,
                "max_calls_7d": settings.risk_max_calls_7d,
                "notes": "Shared gate: payment events share retry cap with all non-invoice agents.",
            },
            "cart_agent": {
                "max_retries": settings.risk_max_retries,
                "cooldown_hours": settings.risk_cooldown_hours,
                "max_calls_7d": settings.risk_max_calls_7d,
                "notes": "Shared gate. Cart also checks per-event voice call history separately.",
            },
            "renewal_agent": {
                "max_retries": settings.risk_max_retries,
                "cooldown_hours": settings.risk_cooldown_hours,
                "ltv_call_threshold_inr": settings.renewal_ltv_call_threshold,
                "max_calls_7d": settings.risk_max_calls_7d,
                "notes": "Shared gate + separate LTV-gated voice call check.",
            },
            "invoice_agent": {
                "max_escalations_30d": settings.invoice_max_escalations_30d,
                "contact_cooldown_hours": settings.invoice_contact_cooldown_hours,
                "notes": "INDEPENDENT gate counters — not shared with payment/cart/renewal caps.",
            },
        },
    }
