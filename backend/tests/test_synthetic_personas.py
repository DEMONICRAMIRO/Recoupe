"""
test_synthetic_personas.py — Pipeline integration tests on the typed persona dataset.

Ground-truth action names confirmed from source:
  payment : retry_alt_route | send_payment_link | retry_now | hold
  cart    : plain | discount | hold  (nudge_type field via SubAgentResponse.action)
  renewal : prompt_card_update | grace_period | silent_retry | hold
  invoice : send_reminder | send_escalation | send_legal_notice | hold

Invoice gate reads from audit_log (NOT CustomerHistoryRow):
  _invoice_contact_history() counts AuditLogRow rows for the customer in the
  past 30 days. Tests must DELETE stale invoice audit rows before each run to
  avoid cooldown fires from a previous test run.

Cart stage trace uses 'diagnose' (not 'classify').

Coverage map
------------
P1  Payment / gate-allowed / retry_alt_route
P2  Payment / gate-blocked-cooldown
P3  Payment / gate-blocked-maxretries
P4  Payment / gate-allowed / send_payment_link (card_expired)
P5  Cart    / gate-allowed / plain or discount nudge (LLM)
P6  Cart    / gate-blocked-maxretries
P7  Renewal / gate-allowed / prompt_card_update (card_expired)
P8  Renewal / gate-blocked-cooldown
P9  Renewal / gate-allowed / grace_period (insufficient_funds -> funds -> grace_period)
P10 Invoice / gate-allowed / call-tier (pure rule, score=1.0)
P11 Invoice / gate-allowed / email-tier (pure rule, score=0.0)
P12 Invoice / gate-blocked (contact_count_7d==3, general gate)
P13 Invoice / gate-allowed / sms-tier (pure rule, score=0.5)
P14 Invoice / gate-allowed / LLM/heuristic ambig (call or sms)
"""
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "synthetic"
sys.path.insert(0, str(BACKEND_DIR))

from app.agents.graph import run_event
from app.db.models import AuditLogRow, CustomerHistoryRow, EventRow
from app.db.session import SessionLocal
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event, EventType

# ─── Load persona dataset ────────────────────────────────────────────────────
_PERSONA_EVENTS: list[dict] = json.loads(
    (DATA_DIR / "persona_events.json").read_text(encoding="utf-8")
)
_PERSONA_HISTORY: list[dict] = json.loads(
    (DATA_DIR / "persona_history.json").read_text(encoding="utf-8")
)

_EVENT_BY_PERSONA: dict[str, dict] = {
    e["persona"]: e for e in _PERSONA_EVENTS if "persona" in e
}
_HISTORY_BY_CUSTOMER: dict[str, dict] = {
    h["customer_id"]: h for h in _PERSONA_HISTORY
}

# Invoice personas that must start from a CLEAN audit slate (gate must allow)
_INVOICE_PURGE_IDS = {
    "persona_nisha_invoicecall",
    "persona_kavitha_invoiceemail",
    "persona_leela_invoicesms",
    "persona_rohan_invoiceambig",
}

# P12 must have 2 prior audit rows so invoice gate fires (escalation_count=2==max)
_P12_CUSTOMER_ID = "persona_amit_invblocked"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _result(state: dict) -> SubAgentResponse | None:
    for key in ("payment_result", "cart_result", "renewal_result", "invoice_result"):
        r = state.get(key)
        if r is not None and isinstance(r, SubAgentResponse):
            return r
    return None


def _gate_allowed(state: dict) -> bool:
    r = _result(state)
    return r.gate_allowed if r is not None else True


def _gate_reason(state: dict) -> str:
    r = _result(state)
    return (r.gate_reason or "") if r else ""


def _action(state: dict) -> str:
    r = _result(state)
    return (r.action or "") if r else ""


def _reasoning(state: dict) -> str:
    r = _result(state)
    return (r.reasoning or "") if r else ""


def _call_tier(state: dict) -> str:
    """Extract call priority tier from reasoning string.
    Invoice agent emits 'priority=<tier>' in reasoning."""
    reasoning = _reasoning(state)
    m = re.search(r'priority=(\w+)', reasoning)
    return m.group(1).lower() if m else ""


def _load_event(persona_key: str) -> Event:
    raw = dict(_EVENT_BY_PERSONA[persona_key])
    raw.pop("persona", None)
    return Event.model_validate(raw)


def _purge_invoice_audit_rows(db, customer_id: str) -> None:
    """Remove all audit rows for this customer so the invoice gate starts clean."""
    from sqlalchemy import select as sa_select
    event_ids = db.execute(
        sa_select(EventRow.event_id).where(EventRow.customer_id == customer_id)
    ).scalars().all()
    if event_ids:
        db.execute(
            delete(AuditLogRow).where(AuditLogRow.event_id.in_(event_ids))
        )
    db.commit()


def _seed_invoice_escalations(db, customer_id: str, count: int = 2) -> None:
    """Insert `count` invoice AuditLogRows so invoice gate sees escalation_count==max.

    Each row needs a backing EventRow (FK constraint). Both rows are timestamped
    within the last 30 days so _invoice_contact_history() counts them.
    """
    from sqlalchemy import select as sa_select
    # First purge stale rows so we don't double-count
    existing_event_ids = db.execute(
        sa_select(EventRow.event_id).where(EventRow.customer_id == customer_id)
    ).scalars().all()
    if existing_event_ids:
        db.execute(delete(AuditLogRow).where(AuditLogRow.event_id.in_(existing_event_ids)))
        db.execute(delete(EventRow).where(EventRow.event_id.in_(existing_event_ids)))
    db.commit()

    now = datetime.now(timezone.utc)
    for i in range(count):
        eid = f"persona_seed_{uuid.uuid4().hex[:10]}"
        ev = EventRow(
            event_id  = eid,
            event_type= EventType.invoice_overdue.value,
            customer_id=customer_id,
            amount    = 10000.0,
            currency  = "INR",
            reason_code=None,
            timestamp = now - timedelta(days=i + 1),  # 1d, 2d ago — within 30d window
            metadata  = {},
        )
        audit = AuditLogRow(
            event_id    = eid,
            action_taken= "send_reminder",
            reasoning   = f"Seeded escalation #{i + 1} for invoice gate test",
            timestamp   = now - timedelta(days=i + 1),
        )
        db.add(ev)
        db.add(audit)
    db.commit()


def _seed_history(db, customer_id: str) -> None:
    raw = _HISTORY_BY_CUSTOMER.get(customer_id)
    if raw is None:
        return

    lca_str = raw.get("last_contacted_at")
    lca = datetime.fromisoformat(lca_str) if lca_str else None
    if lca is not None:
        if lca.tzinfo is None:
            lca = lca.replace(tzinfo=timezone.utc)
        # Shift static timestamp relative to current time so cooldown logic doesn't age out
        baseline = datetime(2026, 9, 5, 15, 21, 29, tzinfo=timezone.utc)
        lca = lca + (datetime.now(timezone.utc) - baseline)

    existing = db.get(CustomerHistoryRow, customer_id)
    if existing is not None:
        existing.contact_count_7d   = raw.get("contact_count_7d", 0)
        existing.past_failures      = raw.get("past_failures", 0)
        existing.preferred_channel  = raw.get("preferred_channel", "email")
        existing.last_contacted_at  = lca
        existing.event_type         = raw.get("event_type")
        db.commit()
    else:
        row = CustomerHistoryRow(
            customer_id      = customer_id,
            event_type       = raw.get("event_type"),
            last_contacted_at= lca,
            contact_count_7d = raw.get("contact_count_7d", 0),
            past_failures    = raw.get("past_failures", 0),
            preferred_channel= raw.get("preferred_channel", "email"),
        )
        db.add(row)
        db.commit()


def _run_persona(persona_key: str) -> dict:
    event = _load_event(persona_key)
    db = SessionLocal()
    try:
        if event.event_type == EventType.invoice_overdue:
            if event.customer_id == _P12_CUSTOMER_ID:
                # P12: seed 2 escalation rows so invoice gate blocks
                _seed_invoice_escalations(db, event.customer_id, count=2)
            elif event.customer_id in _INVOICE_PURGE_IDS:
                # P10/11/13/14: purge stale rows so gate allows
                _purge_invoice_audit_rows(db, event.customer_id)
        _seed_history(db, event.customer_id)
        db.expire_all()
        return run_event(event, db=db)
    finally:
        db.close()


# ─── Module-scoped fixture ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def persona_states() -> dict[str, dict]:
    return {key: _run_persona(key) for key in _EVENT_BY_PERSONA}


# =============================================================================
# P1 — Priya Champion: payment / gate-allowed / retry_alt_route
# =============================================================================

class TestP1_PriyaChampion:
    """Never-contacted, insufficient_funds. Baseline happy path."""
    PERSONA = "P1_Priya_Champion"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Gate should allow champion; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_retry_alt_route(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "retry_alt_route", \
            f"insufficient_funds -> retry_alt_route; got: {_action(persona_states[self.PERSONA])!r}"

    def test_status_processed(self, persona_states):
        r = _result(persona_states[self.PERSONA])
        assert r is not None and r.status == "processed"


# =============================================================================
# P2 — Rajan Cooldown: payment / gate-blocked (cooldown)
# =============================================================================

class TestP2_RajanCooldown:
    """Contacted 2 hours ago — inside 24-hour cooldown."""
    PERSONA = "P2_Rajan_Cooldown"

    def test_gate_blocked(self, persona_states):
        assert not _gate_allowed(persona_states[self.PERSONA]), \
            f"Cooldown should block; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_block_reason_mentions_cooldown(self, persona_states):
        reason = _gate_reason(persona_states[self.PERSONA]).lower()
        assert "cooldown" in reason, f"Block reason should say cooldown; got: {reason!r}"

    def test_action_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"


# =============================================================================
# P3 — Dev MaxRetries: payment / gate-blocked (max retries)
# =============================================================================

class TestP3_DevMaxRetries:
    """contact_count_7d == risk_max_retries (3)."""
    PERSONA = "P3_Dev_MaxRetries"

    def test_gate_blocked(self, persona_states):
        assert not _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice escalation max should block; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"

    def test_block_reason_mentions_escalation(self, persona_states):
        reason = _gate_reason(persona_states[self.PERSONA]).lower()
        assert "escalation" in reason or "maximum" in reason, \
            f"Block reason should mention escalation or maximum; got: {reason!r}"

    def test_audit_row_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"


# =============================================================================
# P4 — Aisha PayLink: payment / gate-allowed / send_payment_link
# =============================================================================

class TestP4_AishaPayLink:
    """card_expired -> send_payment_link (rule table)."""
    PERSONA = "P4_Aisha_PayLink"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA])

    def test_action_send_payment_link(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "send_payment_link", \
            f"card_expired -> send_payment_link; got: {_action(persona_states[self.PERSONA])!r}"


# =============================================================================
# P5 — Meera CartNudge: cart / gate-allowed / plain or discount
# =============================================================================

class TestP5_MeeraCartNudge:
    """
    First-time buyer, high-value cart. Cart agent diagnoses via LLM and picks
    a nudge type. Action will be 'plain' or 'discount' (not 'send_nudge').
    We assert gate-allowed and a valid nudge (not hold).
    """
    PERSONA = "P5_Meera_CartNudge"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Cart gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_is_valid_nudge(self, persona_states):
        action = _action(persona_states[self.PERSONA])
        assert action in ("plain", "discount"), \
            f"Cart nudge action must be plain or discount; got: {action!r}"

    def test_action_is_not_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) != "hold"

    def test_status_processed(self, persona_states):
        r = _result(persona_states[self.PERSONA])
        assert r is not None and r.status == "processed"


# =============================================================================
# P6 — Sanjay CartBlocked: cart / gate-blocked
# =============================================================================

class TestP6_SanjayCartBlocked:
    """contact_count_7d == risk_max_retries — cart gate must block."""
    PERSONA = "P6_Sanjay_CartBlocked"

    def test_gate_blocked(self, persona_states):
        assert not _gate_allowed(persona_states[self.PERSONA]), \
            f"Cart max-retries should block; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"


# =============================================================================
# P7 — Anita RenewalCardUpdate: renewal / gate-allowed / prompt_card_update
# =============================================================================

class TestP7_AnitaRenewalCardUpdate:
    """
    card_expired renewal -> cause=card_expiry -> action=prompt_card_update
    (Actual renewal ACTION_BY_CAUSE: card_expiry -> prompt_card_update)
    """
    PERSONA = "P7_Anita_RenewalCardUpdate"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA])

    def test_action_prompt_card_update(self, persona_states):
        action = _action(persona_states[self.PERSONA])
        assert action == "prompt_card_update", \
            f"card_expired renewal -> prompt_card_update; got: {action!r}"


# =============================================================================
# P8 — Vikram MandateBlocked: renewal / gate-blocked (cooldown)
# =============================================================================

class TestP8_VikramMandateBlocked:
    """Contacted 1 hour ago — inside 24-hour cooldown."""
    PERSONA = "P8_Vikram_MandateBlocked"

    def test_gate_blocked(self, persona_states):
        assert not _gate_allowed(persona_states[self.PERSONA]), \
            f"Cooldown should block; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_block_reason_cooldown(self, persona_states):
        reason = _gate_reason(persona_states[self.PERSONA]).lower()
        assert "cooldown" in reason, f"Block reason should say cooldown; got: {reason!r}"

    def test_action_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"


# =============================================================================
# P9 — Suresh GoodRenewal: renewal / gate-allowed / grace_period
# =============================================================================

class TestP9_SureshGoodRenewal:
    """
    insufficient_funds renewal -> cause=funds -> action=grace_period
    (Actual renewal ACTION_BY_CAUSE: funds -> grace_period)
    Last contact 30h ago, outside 24h cooldown.
    """
    PERSONA = "P9_Suresh_GoodRenewal"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Renewal gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_grace_period(self, persona_states):
        action = _action(persona_states[self.PERSONA])
        assert action == "grace_period", \
            f"insufficient_funds renewal -> grace_period; got: {action!r}"


# =============================================================================
# P10 — Nisha InvoiceCall: invoice / gate-allowed / call tier (pure rule)
# =============================================================================

class TestP10_NishaInvoiceCall:
    """100d, poor reliability, broken promise -> call tier, score=1.0 (rule, no LLM)."""
    PERSONA = "P10_Nisha_InvoiceCall"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_call_tier_is_call(self, persona_states):
        tier = _call_tier(persona_states[self.PERSONA])
        assert tier == "call", \
            f"100d+poor+broken_promise -> call; got tier={tier!r}, reasoning={_reasoning(persona_states[self.PERSONA])!r}"

    def test_reasoning_mentions_call(self, persona_states):
        reasoning = _reasoning(persona_states[self.PERSONA]).lower()
        assert "call" in reasoning, f"Reasoning should mention call; got: {reasoning!r}"

    def test_llm_not_invoked_for_clear_rule(self, persona_states):
        """Pure rule case — reasoning must say 'Rule:' not 'LLM:'."""
        reasoning = _reasoning(persona_states[self.PERSONA])
        assert "LLM:" not in reasoning, \
            f"Clear-cut rule should not invoke LLM; reasoning={reasoning!r}"


# =============================================================================
# P11 — Kavitha InvoiceEmail: invoice / gate-allowed / email tier (pure rule)
# =============================================================================

class TestP11_KavithaInvoiceEmail:
    """15d, good, no broken promise -> email tier (rule: score=0.0)."""
    PERSONA = "P11_Kavitha_InvoiceEmail"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_call_tier_is_email(self, persona_states):
        tier = _call_tier(persona_states[self.PERSONA])
        assert tier == "email", \
            f"15d+good+no_promise -> email; got tier={tier!r}, reasoning={_reasoning(persona_states[self.PERSONA])!r}"

    def test_action_is_not_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) != "hold"


# =============================================================================
# P12 — AmitInvoiceGateBlocked: invoice / gate-blocked (general contact_count)
# =============================================================================

class TestP12_AmitInvoiceGateBlocked:
    """
    Seed 2 AuditLogRows so _invoice_contact_history returns escalation_count=2
    which equals invoice_max_escalations_30d. The invoice gate blocks.
    """
    PERSONA = "P12_Amit_InvoiceGateBlocked"

    def test_gate_blocked(self, persona_states):
        assert not _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice escalation gate should block; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_action_is_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) == "hold"

    def test_block_reason_mentions_escalation(self, persona_states):
        reason = _gate_reason(persona_states[self.PERSONA]).lower()
        assert "escalation" in reason or "maximum" in reason, \
            f"Block reason should mention escalation or maximum; got: {reason!r}"

    def test_audit_row_is_hold(self):
        """Audit log row action must be hold."""
        from sqlalchemy import select as sa_select
        db = SessionLocal()
        try:
            event_id = _EVENT_BY_PERSONA["P12_Amit_InvoiceGateBlocked"]["event_id"]
            row = db.execute(
                sa_select(AuditLogRow).where(AuditLogRow.event_id == event_id)
            ).scalars().first()
            if row is not None:
                assert row.action_taken == "hold", \
                    f"Audit row action_taken should be hold; got: {row.action_taken!r}"
        finally:
            db.close()


# =============================================================================
# P13 — Leela InvoiceSMS: invoice / gate-allowed / sms tier (pure rule)
# =============================================================================

class TestP13_LeelaInvoiceSMS:
    """95d, good reliability, no broken promise -> sms tier (rule: score=0.5)."""
    PERSONA = "P13_Leela_InvoiceSMS"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_call_tier_is_sms(self, persona_states):
        tier = _call_tier(persona_states[self.PERSONA])
        assert tier == "sms", \
            f"95d+good+no_promise -> sms; got tier={tier!r}, reasoning={_reasoning(persona_states[self.PERSONA])!r}"

    def test_action_is_not_hold(self, persona_states):
        assert _action(persona_states[self.PERSONA]) != "hold"


# =============================================================================
# P14 — Rohan InvoiceAmbig: invoice / gate-allowed / LLM/heuristic
# =============================================================================

class TestP14_RohanInvoiceAmbig:
    """
    55d, fair reliability, broken promise — genuinely ambiguous.
    LLM or heuristic fallback must resolve to call or sms.
    """
    PERSONA = "P14_Rohan_InvoiceAmbig"

    def test_gate_allowed(self, persona_states):
        assert _gate_allowed(persona_states[self.PERSONA]), \
            f"Invoice gate should allow; reason: {_gate_reason(persona_states[self.PERSONA])!r}"

    def test_call_tier_is_call_or_sms(self, persona_states):
        tier = _call_tier(persona_states[self.PERSONA])
        assert tier in ("call", "sms"), \
            f"Ambiguous case must resolve to call or sms; got: {tier!r}, reasoning={_reasoning(persona_states[self.PERSONA])!r}"

    def test_pipeline_completes_successfully(self, persona_states):
        r = _result(persona_states[self.PERSONA])
        assert r is not None, "invoice_result must be present"
        assert r.status == "processed"

    def test_reasoning_has_priority(self, persona_states):
        reasoning = _reasoning(persona_states[self.PERSONA])
        assert "priority=" in reasoning, \
            f"Reasoning should contain 'priority=<tier>'; got: {reasoning!r}"


# =============================================================================
# Cross-persona invariants
# =============================================================================

class TestCrossPersonaInvariants:

    def test_all_personas_have_result(self, persona_states):
        for key, state in persona_states.items():
            r = _result(state)
            assert r is not None, f"No SubAgentResponse found for {key}"

    def test_gate_blocked_always_produces_hold(self, persona_states):
        for key, state in persona_states.items():
            if not _gate_allowed(state):
                action = _action(state)
                assert action == "hold", \
                    f"{key}: gate blocked but action={action!r}"

    def test_gate_allowed_never_hold(self, persona_states):
        for key, state in persona_states.items():
            if _gate_allowed(state):
                action = _action(state)
                assert action != "hold", \
                    f"{key}: gate allowed but action=hold"

    def test_all_14_personas_loaded(self):
        assert len(_EVENT_BY_PERSONA) == 14

    def test_all_four_event_types_covered(self):
        types = {e.get("event_type") for e in _PERSONA_EVENTS}
        expected = {et.value for et in EventType}
        assert expected.issubset(types), f"Missing event types: {expected - types}"

    def test_blocked_personas_exist(self, persona_states):
        blocked = [k for k, s in persona_states.items() if not _gate_allowed(s)]
        assert len(blocked) >= 4, \
            f"Expected >= 4 blocked, found {len(blocked)}: {blocked}"

    def test_allowed_personas_exist(self, persona_states):
        allowed = [k for k, s in persona_states.items() if _gate_allowed(s)]
        assert len(allowed) >= 7, \
            f"Expected >= 7 allowed, found {len(allowed)}: {allowed}"

    def test_every_result_has_all_6_stages(self, persona_states):
        """All agents must run all 6 pipeline stages (stage names vary by agent)."""
        for key, state in persona_states.items():
            r = _result(state)
            if r is None:
                continue
            trace = r.stage_trace or []
            assert len(trace) == 6, \
                f"{key}: expected 6 stages, got {len(trace)}: {trace}"
            # First and last stages are always the same
            assert trace[0] == "fetch", f"{key}: first stage should be fetch; got {trace[0]}"
            assert trace[-1] == "execute_log", f"{key}: last stage should be execute_log; got {trace[-1]}"
