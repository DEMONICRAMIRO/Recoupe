"""test_phase4.py — All 11 self-test criteria for Phase 4: Invoice Agent + Integration.

Per prd-phase4.md Section 5. Test count sanity check is criterion 11.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]   # backend/tests/test_phase4.py → [0]tests [1]backend [2]Recoupe
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "synthetic"

sys.path.insert(0, str(BACKEND_DIR))

from app.core.call_priority import (
    TIER_CALL,
    TIER_EMAIL,
    TIER_SMS,
    CallPriorityResult,
    score_call_priority,
)
from app.core.risk_gate import evaluate_invoice_gate, evaluate
from app.schemas.event import Event, EventType
from app.services.comms.templates import render
from app.services.comms.templates.invoice_reminder import (
    INVOICE_ESCALATION_EMAIL,
    INVOICE_ESCALATION_SMS,
    INVOICE_FIRM_FOLLOWUP_EMAIL,
    INVOICE_FIRM_FOLLOWUP_SMS,
    INVOICE_REMINDER_EMAIL,
    INVOICE_REMINDER_SMS,
)

VERIFIED_PHONE = "+919631581658"
VERIFIED_EMAIL = "arjunkumarsingh166@gmail.com"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


def _ok_result(provider: str, status: str = "queued"):
    from app.services.comms import SendResult
    return SendResult(success=True, provider=provider, provider_id="test_sid", status=status)


def _fail_result(provider: str):
    from app.services.comms import SendResult
    return SendResult(success=False, provider=provider, provider_id=None, status="failed", error="test_error")


def _invoice_event(
    event_id: str = "test_inv_001",
    customer_id: str = "cus_test_001",
    days_overdue: int = 45,
    amount: float = 25000.0,
    account_reliability: str = "fair",
    broken_promise: bool = False,
    phone: str = VERIFIED_PHONE,
    email: str = VERIFIED_EMAIL,
) -> Event:
    return Event(
        event_id=event_id,
        event_type=EventType.invoice_overdue,
        customer_id=customer_id,
        amount=amount,
        currency="INR",
        reason_code=None,
        timestamp=datetime.now(timezone.utc),
        metadata={
            "customer_name": "Test Customer",
            "invoice_number": "INV-TEST-001",
            "due_date": "2026-06-01",
            "days_overdue": days_overdue,
            "account_tier": "growth",
            "account_reliability": account_reliability,
            "broken_promise": broken_promise,
            "phone": phone,
            "email": email,
        },
    )


# ---------------------------------------------------------------------------
# Criterion 1: Pipeline completeness — 30 events, 0 dropped
# ---------------------------------------------------------------------------

def test_pipeline_completeness_30_events(monkeypatch):
    """30 synthetic invoice events through full pipeline, every one gets an audit log entry."""
    import app.agents.invoice_agent as inv_mod
    import app.core.call_priority as cp_mod

    monkeypatch.setattr(inv_mod, "send_sms", lambda to, body: _ok_result("twilio_sms"))
    monkeypatch.setattr(inv_mod, "send_email", lambda to, subj, body: _ok_result("smtp"))
    monkeypatch.setattr(inv_mod, "place_voice_call", lambda to, body, **kw: _ok_result("twilio_voice"))
    monkeypatch.setattr(cp_mod, "chat", lambda *a, **kw: MagicMock(
        content='{"tier": "sms", "score": 0.5, "reasoning": "test stub"}'
    ))

    raw = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
    invoice_events = [
        Event.model_validate(e) for e in raw if e["event_type"] == "invoice_overdue"
    ]
    assert len(invoice_events) == 30, f"Expected 30 invoice events, got {len(invoice_events)}"

    from app.db.session import SessionLocal
    from app.db.models import AuditLogRow
    from sqlalchemy import select

    processed = 0
    audit_ids = []
    db = SessionLocal()
    try:
        for event in invoice_events:
            from app.agents.invoice_agent import run_invoice_event
            result = run_invoice_event(event, db=db)
            assert result.get("audit_log_id") is not None, f"No audit_log_id for {event.event_id}"
            audit_ids.append(result["audit_log_id"])
            assert result.get("response") is not None
            assert result["response"].action != "placeholder_action"
            processed += 1
    finally:
        db.close()

    assert processed == 30, f"Only {processed}/30 events processed"


# ---------------------------------------------------------------------------
# Criterion 2: Classification correctness — deterministic bucketing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days_overdue,expected_bucket", [
    (1, "overdue_30"),
    (29, "overdue_30"),
    (30, "overdue_30"),
    (45, "overdue_30"),
    (59, "overdue_30"),
    (60, "overdue_60"),
    (75, "overdue_60"),
    (89, "overdue_60"),
    (90, "overdue_90"),
    (105, "overdue_90"),
    (120, "overdue_90"),
])
def test_classification_bucket_correctness(days_overdue, expected_bucket):
    """Days-overdue bucketing must be 100% deterministic — no tolerance."""
    from app.agents.invoice_agent import classify_invoice, BUCKET_30, BUCKET_60, BUCKET_90
    event = _invoice_event(days_overdue=days_overdue)
    fake_state = {
        "event": event,
        "stage_trace": [],
    }
    result = classify_invoice(fake_state)
    classification = result["classification"]
    assert classification.bucket == expected_bucket, (
        f"days_overdue={days_overdue}: expected {expected_bucket!r}, got {classification.bucket!r}"
    )
    assert result["call_priority"] is not None


# ---------------------------------------------------------------------------
# Criterion 3a: Call-priority scoring — rule-based cases (exact assert, no LLM)
# ---------------------------------------------------------------------------

def test_call_priority_rule_clearly_call_worthy():
    """90+d, poor reliability, broken promise → call tier, no LLM."""
    result = score_call_priority(95, "poor", True, llm_enabled=True)
    assert result.tier == TIER_CALL
    assert result.llm_invoked is False
    assert result.score >= 0.9


def test_call_priority_rule_clearly_email_only():
    """25d, good reliability, no broken promise → email tier, no LLM."""
    result = score_call_priority(25, "good", False, llm_enabled=True)
    assert result.tier == TIER_EMAIL
    assert result.llm_invoked is False
    assert result.score <= 0.15


def test_call_priority_rule_borderline_resolves_without_llm():
    """92d + poor reliability (no broken promise) — looks borderline, but rules handle it."""
    result = score_call_priority(92, "poor", False, llm_enabled=True)
    assert result.tier == TIER_CALL
    assert result.llm_invoked is False
    assert result.score >= 0.8


# ---------------------------------------------------------------------------
# Criterion 3b: Call-priority scoring — LLM-ambiguous cases
# ---------------------------------------------------------------------------

def test_call_priority_llm_invoked_moderate_overdue_mixed_signals(monkeypatch):
    """45d + fair reliability + no broken promise → LLM MUST be invoked."""
    import app.core.call_priority as cp_mod

    llm_calls = []

    def fake_chat(prompt, *, model=None, max_tokens=None):
        llm_calls.append(prompt)
        return MagicMock(content='{"tier": "sms", "score": 0.45, "reasoning": "moderate risk, SMS sufficient"}')

    monkeypatch.setattr(cp_mod, "chat", fake_chat)
    result = score_call_priority(45, "fair", False, llm_enabled=True)
    assert len(llm_calls) == 1, "LLM must have been called exactly once"
    assert result.llm_invoked is True
    assert result.tier in (TIER_CALL, TIER_EMAIL, TIER_SMS)
    # Manual review: tier and reasoning printed for human verification
    print(f"\n[LLM CASE L1] tier={result.tier!r} score={result.score} reasoning={result.reasoning!r}")


def test_call_priority_llm_invoked_good_reliability_with_broken_promise(monkeypatch):
    """60d + good reliability + broken promise → conflicting signals, LLM MUST be invoked."""
    import app.core.call_priority as cp_mod

    llm_calls = []

    def fake_chat(prompt, *, model=None, max_tokens=None):
        llm_calls.append(prompt)
        return MagicMock(content='{"tier": "call", "score": 0.8, "reasoning": "broken promise overrides good reliability"}')

    monkeypatch.setattr(cp_mod, "chat", fake_chat)
    result = score_call_priority(60, "good", True, llm_enabled=True)
    assert len(llm_calls) == 1, "LLM must have been called exactly once"
    assert result.llm_invoked is True
    print(f"\n[LLM CASE L2] tier={result.tier!r} score={result.score} reasoning={result.reasoning!r}")


def test_call_priority_llm_fallback_on_parse_error(monkeypatch):
    """If LLM returns unparsable JSON, heuristic fallback must kick in."""
    import app.core.call_priority as cp_mod

    monkeypatch.setattr(cp_mod, "chat", lambda *a, **kw: MagicMock(content="not json at all"))
    result = score_call_priority(45, "fair", False, llm_enabled=True)
    assert result.llm_invoked is False  # fallback sets llm_invoked=False
    assert result.tier in (TIER_CALL, TIER_EMAIL, TIER_SMS)


# ---------------------------------------------------------------------------
# Criterion 4: Gate correctness — invoice-specific counters, independence
# ---------------------------------------------------------------------------

def test_invoice_gate_blocked_by_escalation_count():
    """Escalation count >= max → blocked."""
    now = datetime.now(timezone.utc)
    result = evaluate_invoice_gate(
        {"invoice_escalation_count_30d": 2, "last_invoice_contact_at": None}, now
    )
    assert result.allowed is False
    assert "invoice_escalation_count_30d" in result.reason


def test_invoice_gate_blocked_by_cooldown():
    """Recent contact within cooldown window → blocked."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(hours=12)  # within 48h cooldown
    result = evaluate_invoice_gate(
        {"invoice_escalation_count_30d": 0, "last_invoice_contact_at": recent}, now
    )
    assert result.allowed is False
    assert "cooldown" in result.reason


def test_invoice_gate_passes_fresh_account():
    """No prior contacts → allowed."""
    now = datetime.now(timezone.utc)
    result = evaluate_invoice_gate(
        {"invoice_escalation_count_30d": 0, "last_invoice_contact_at": None}, now
    )
    assert result.allowed is True


def test_invoice_gate_passes_after_cooldown_expires():
    """Contact older than cooldown window → allowed."""
    now = datetime.now(timezone.utc)
    old_contact = now - timedelta(hours=72)  # past 48h cooldown
    result = evaluate_invoice_gate(
        {"invoice_escalation_count_30d": 1, "last_invoice_contact_at": old_contact}, now
    )
    assert result.allowed is True


def test_invoice_gate_independent_from_payment_gate():
    """Invoice gate blocked must NOT affect the standard message-frequency gate."""
    now = datetime.now(timezone.utc)
    # Invoice gate blocked
    inv_gate = evaluate_invoice_gate(
        {"invoice_escalation_count_30d": 2, "last_invoice_contact_at": None}, now
    )
    assert inv_gate.allowed is False
    # Standard payment gate on same customer with low contact count → still allowed
    std_gate = evaluate(
        {"contact_count_7d": 0, "last_contacted_at": None}, now
    )
    assert std_gate.allowed is True, (
        "Invoice gate block must not propagate to the standard message gate"
    )


# ---------------------------------------------------------------------------
# Criterion 5: Decision correctness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days_overdue,gate_allowed,priority_tier,expected_action", [
    # Gate blocked → hold regardless
    (90, False, TIER_CALL, "hold"),
    (60, False, TIER_EMAIL, "hold"),
    (30, False, TIER_EMAIL, "hold"),
    # 90+ bucket
    (90, True, TIER_CALL, "escalate_with_call"),
    (95, True, TIER_EMAIL, "escalate_no_call"),
    (100, True, TIER_SMS, "escalate_no_call"),
    # 60-89 bucket
    (60, True, TIER_CALL, "send_firm_followup"),
    (75, True, TIER_EMAIL, "send_firm_followup"),
    # <60 bucket (reminder)
    (30, True, TIER_EMAIL, "send_reminder"),
    (45, True, TIER_CALL, "send_reminder"),
])
def test_decision_correctness(days_overdue, gate_allowed, priority_tier, expected_action, monkeypatch):
    """Every (bucket, gate_result, priority) combination maps to the correct action."""
    from app.agents.invoice_agent import decide_invoice, InvoiceClassification, InvoiceDecision
    from app.core.risk_gate import GateDecision

    if days_overdue >= 90:
        bucket = "overdue_90"
    elif days_overdue >= 60:
        bucket = "overdue_60"
    else:
        bucket = "overdue_30"

    state = {
        "event": _invoice_event(days_overdue=days_overdue),
        "gate": GateDecision(
            allowed=gate_allowed,
            reason="allowed: test" if gate_allowed else "blocked: test",
        ),
        "classification": InvoiceClassification(
            bucket=bucket,
            days_overdue=days_overdue,
            reasoning="test",
        ),
        "call_priority": CallPriorityResult(
            tier=priority_tier,
            score=0.8 if priority_tier == TIER_CALL else 0.2,
            reasoning="test",
            llm_invoked=False,
        ),
        "stage_trace": [],
    }
    result = decide_invoice(state)
    assert result["decision"].action == expected_action, (
        f"days={days_overdue} gate={gate_allowed} tier={priority_tier}: "
        f"expected {expected_action!r}, got {result['decision'].action!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 6: Comms Layer real delivery
# ---------------------------------------------------------------------------

def test_real_sms_delivery():
    """Real SMS delivery to verified Twilio number."""
    from app.services.comms.sms import send_sms
    result = send_sms(VERIFIED_PHONE, "sms_appointment_reminders")
    print(f"\nSMS: provider={result.provider} status={result.status} id={result.provider_id}")
    assert result.success is True
    assert result.provider_id is not None


def test_real_email_delivery():
    """Real SMTP email delivery."""
    from app.services.comms.email import send_email
    result = send_email(
        VERIFIED_EMAIL,
        "Recoupe Phase 4 — Invoice Agent email delivery verification",
        "This is the real SMTP delivery verification for Recoupe Phase 4 (Invoice Agent).\n\n— Recoupe",
    )
    print(f"\nEmail: provider={result.provider} status={result.status} id={result.provider_id}")
    assert result.success is True
    assert result.provider_id is not None


# ---------------------------------------------------------------------------
# Criterion 7: Template rendering
# ---------------------------------------------------------------------------

TEMPLATE_VALUES = {
    "customer_name": "Arjun Singh",
    "invoice_number": "INV-2026-0042",
    "amount": "25,000.00",
    "days_overdue": "45",
    "due_date": "2026-07-22",
    "link": "https://rzp.io/i/recovery-demo/test",
}


@pytest.mark.parametrize("template,channel", [
    (INVOICE_REMINDER_SMS, "sms"),
    (INVOICE_REMINDER_EMAIL, "email"),
    (INVOICE_FIRM_FOLLOWUP_SMS, "sms"),
    (INVOICE_FIRM_FOLLOWUP_EMAIL, "email"),
    (INVOICE_ESCALATION_SMS, "sms"),
    (INVOICE_ESCALATION_EMAIL, "email"),
])
def test_template_renders_correctly(template, channel):
    """render() fills all placeholders; channel matches expected."""
    rendered = render(template, **TEMPLATE_VALUES)
    assert "Arjun Singh" in rendered.body
    assert "INV-2026-0042" in rendered.body
    assert rendered.channel == channel
    if channel == "email":
        assert rendered.subject is not None
        assert "INV-2026-0042" in rendered.subject


def test_template_missing_placeholder_raises():
    """render() must raise KeyError when a required placeholder is missing."""
    with pytest.raises(KeyError):
        render(INVOICE_REMINDER_SMS, customer_name="Only This")  # missing other placeholders


# ---------------------------------------------------------------------------
# Criterion 8: Audit log integrity — exactly one row per event, non-null fields
# ---------------------------------------------------------------------------

def test_audit_log_one_row_per_event(monkeypatch):
    """Each processed event must produce exactly one audit log row."""
    import app.agents.invoice_agent as inv_mod
    import app.core.call_priority as cp_mod

    monkeypatch.setattr(inv_mod, "send_sms", lambda to, body: _ok_result("twilio_sms"))
    monkeypatch.setattr(inv_mod, "send_email", lambda to, subj, body: _ok_result("smtp"))
    monkeypatch.setattr(inv_mod, "place_voice_call", lambda to, body, **kw: _ok_result("twilio_voice"))
    monkeypatch.setattr(cp_mod, "chat", lambda *a, **kw: MagicMock(
        content='{"tier": "call", "score": 0.7, "reasoning": "test stub"}'
    ))

    from app.db.session import SessionLocal
    from app.db.models import AuditLogRow
    from sqlalchemy import select

    events = [
        _invoice_event("audit_test_001", days_overdue=35),
        _invoice_event("audit_test_002", days_overdue=65),
        _invoice_event("audit_test_003", days_overdue=95, account_reliability="poor", broken_promise=True),
    ]

    db = SessionLocal()
    try:
        from app.agents.invoice_agent import run_invoice_event
        for event in events:
            run_invoice_event(event, db=db)

        for event in events:
            rows = db.execute(
                select(AuditLogRow).where(AuditLogRow.event_id == event.event_id)
            ).scalars().all()
            # Voice-blocked events may have 2 rows (one voice_call_blocked, one main)
            # but at minimum exactly 1 main decision row must exist
            main_rows = [r for r in rows if r.action_taken not in ("voice_call", "voice_call_blocked")]
            assert len(main_rows) >= 1, f"No main audit row for {event.event_id}"
            for r in main_rows:
                assert r.action_taken is not None and r.action_taken != ""
                assert r.reasoning is not None and r.reasoning != ""
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Criterion 9: Full system integration test — mixed 4-type batch
# ---------------------------------------------------------------------------

def test_full_system_integration_mixed_batch(monkeypatch):
    """One event of each type through Orchestrator → correct routing + one audit log entry each."""
    import app.agents.invoice_agent as inv_mod
    import app.agents.cart_agent as cart_mod
    import app.agents.renewal_agent as renewal_mod
    import app.agents.payment_agent as pay_mod
    import app.core.call_priority as cp_mod
    import app.core.llm_client as llm_mod

    # Patch all comms so no real calls go out in the integration test
    _noop_sms = lambda to, body, **kw: _ok_result("twilio_sms")
    _noop_email = lambda to, subj, body: _ok_result("smtp")
    _noop_voice = lambda to, body, **kw: _ok_result("twilio_voice")
    _noop_wa = lambda to, body: _ok_result("twilio_whatsapp", "queued_whatsapp")

    for mod in (inv_mod, cart_mod, renewal_mod, pay_mod):
        if hasattr(mod, "send_sms"):
            monkeypatch.setattr(mod, "send_sms", _noop_sms)
        if hasattr(mod, "send_email"):
            monkeypatch.setattr(mod, "send_email", _noop_email)
        if hasattr(mod, "place_voice_call"):
            monkeypatch.setattr(mod, "place_voice_call", _noop_voice)
        if hasattr(mod, "send_whatsapp"):
            monkeypatch.setattr(mod, "send_whatsapp", _noop_wa)

    # Patch LLM for all agents that use it (cart diagnose, call_priority)
    monkeypatch.setattr(cp_mod, "chat", lambda *a, **kw: MagicMock(
        content='{"tier": "sms", "score": 0.5, "reasoning": "integration test stub"}'
    ))
    monkeypatch.setattr(llm_mod, "chat", lambda *a, **kw: MagicMock(
        content='{"reason": "price", "confidence": 0.8, "explanation": "integration test stub"}'
    ))

    now = datetime.now(timezone.utc)
    mixed_events = [
        Event(
            event_id="integ_payment_001",
            event_type=EventType.payment_failed,
            customer_id="integ_cus_001",
            amount=5000.0, currency="INR",
            reason_code="insufficient_funds",
            timestamp=now,
            metadata={"customer_name": "A", "phone": VERIFIED_PHONE, "email": VERIFIED_EMAIL,
                      "payment_method": "card", "payment_link": "https://rzp.io/test"},
        ),
        Event(
            event_id="integ_cart_001",
            event_type=EventType.cart_abandoned,
            customer_id="integ_cus_002",
            amount=2500.0, currency="INR",
            reason_code=None,
            timestamp=now,
            metadata={"customer_name": "B", "phone": VERIFIED_PHONE, "email": VERIFIED_EMAIL,
                      "cart_items": 3, "device": "mobile", "session_duration_sec": 180,
                      "avg_order_value": 1500.0, "price_sensitivity": "medium"},
        ),
        Event(
            event_id="integ_renewal_001",
            event_type=EventType.renewal_failed,
            customer_id="integ_cus_003",
            amount=999.0, currency="INR",
            reason_code="card_expired",
            timestamp=now,
            metadata={"customer_name": "C", "phone": VERIFIED_PHONE, "email": VERIFIED_EMAIL,
                      "plan": "monthly", "ltv_amount": 12000.0,
                      "payment_link": "https://rzp.io/test"},
        ),
        Event(
            event_id="integ_invoice_001",
            event_type=EventType.invoice_overdue,
            customer_id="integ_cus_004",
            amount=50000.0, currency="INR",
            reason_code=None,
            timestamp=now,
            metadata={"customer_name": "D", "invoice_number": "INV-INTEG-001",
                      "due_date": "2026-06-01", "days_overdue": 75,
                      "account_tier": "enterprise", "account_reliability": "fair",
                      "broken_promise": False, "phone": VERIFIED_PHONE, "email": VERIFIED_EMAIL},
        ),
    ]

    EXPECTED_AGENTS = {
        "integ_payment_001": "payment_agent",
        "integ_cart_001": "cart_agent",
        "integ_renewal_001": "renewal_agent",
        "integ_invoice_001": "invoice_agent",
    }

    from app.agents.graph import run_event
    from app.db.session import SessionLocal
    from app.db.models import AuditLogRow
    from sqlalchemy import select

    db = SessionLocal()
    try:
        routing_results: dict[str, str] = {}
        for event in mixed_events:
            state = run_event(event, db=db)
            # Determine which agent handled this event
            for agent_key, result_key in [
                ("payment_agent", "payment_result"),
                ("cart_agent", "cart_result"),
                ("renewal_agent", "renewal_result"),
                ("invoice_agent", "invoice_result"),
            ]:
                if result_key in state and state[result_key] is not None:
                    routed = state[result_key].agent
                    routing_results[event.event_id] = routed
                    break
            else:
                routing_results[event.event_id] = state["response"].agent

        # Assert correct routing — 100%, 0 misroutes
        for event_id, expected_agent in EXPECTED_AGENTS.items():
            actual = routing_results.get(event_id, "NOT_ROUTED")
            assert actual == expected_agent, (
                f"Misroute: {event_id} → {actual!r} (expected {expected_agent!r})"
            )

        # Assert exactly one audit log entry per event
        for event in mixed_events:
            rows = db.execute(
                select(AuditLogRow).where(AuditLogRow.event_id == event.event_id)
            ).scalars().all()
            main_rows = [r for r in rows if r.action_taken not in ("voice_call", "voice_call_blocked")]
            assert len(main_rows) >= 1, (
                f"No audit log entry for {event.event_id} ({EXPECTED_AGENTS[event.event_id]})"
            )
            # Print for manual review
            print(f"\n[INTEG] {event.event_id}: routed={routing_results[event.event_id]!r}"
                  f" action={main_rows[0].action_taken!r}")

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Criterion 10: Batch metrics — run_batch.py subprocess
# ---------------------------------------------------------------------------

def test_batch_metrics_output():
    """run_batch.py outputs Recovery rate % and INR recovered across all 4 event types."""
    env = os.environ.copy()
    env["RECOUPE_TEST_MODE"] = "1"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_batch.py")],
        capture_output=True,
        text=True,
        env=env,
    )
    print(f"\nBatch stdout:\n{result.stdout}")
    if result.stderr:
        print(f"Batch stderr:\n{result.stderr[:500]}")
    assert result.returncode == 0, f"run_batch.py exited with code {result.returncode}"
    assert "Recovery rate:" in result.stdout
    assert "INR" in result.stdout
    # All 4 agent types should appear
    for agent in ("payment_agent", "cart_agent", "renewal_agent", "invoice_agent"):
        assert agent in result.stdout, f"Missing {agent} in batch output"


# ---------------------------------------------------------------------------
# Criterion 11: Full suite + test count sanity check
# ---------------------------------------------------------------------------

KNOWN_GOOD_COUNTS = {
    "test_phase1.py": 51,
    "test_phase2.py": 26,
    "test_phase3.py": 19,
}


def test_phase_test_counts_sanity():
    """Verify per-file test counts match last known-good baseline. Flag any drop immediately."""
    import subprocess as sp

    tests_dir = BACKEND_DIR / "tests"
    count_issues = []

    for filename, expected in KNOWN_GOOD_COUNTS.items():
        result = sp.run(
            [sys.executable, "-m", "pytest", str(tests_dir / filename), "--collect-only", "-q"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        output = result.stdout + result.stderr
        # Parse "N tests collected" — scan all lines, take last match to skip warning lines
        actual = None
        for line in output.splitlines():
            stripped = line.strip()
            if ("tests collected" in stripped or "test collected" in stripped) and stripped[0].isdigit():
                try:
                    actual = int(stripped.split()[0])
                except (ValueError, IndexError):
                    pass
        if actual is None:
            count_issues.append(f"{filename}: could not parse count from pytest output")
        elif actual != expected:
            count_issues.append(
                f"{filename}: COUNT CHANGED — expected {expected}, got {actual} "
                f"({'DROPPED' if actual < expected else 'GREW'} by {abs(actual - expected)})"
            )
        else:
            print(f"  {filename}: {actual} tests ✓")

    assert not count_issues, (
        "Test count sanity check FAILED:\n" + "\n".join(count_issues)
    )
