import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agents.payment_agent import (
    ACTION_BY_CAUSE,
    CAUSE_BY_REASON_CODE,
    PaymentDecision,
    PaymentDiagnosis,
    classify_payment,
    decide_payment,
    run_payment_event,
)
from app.core.risk_gate import GateDecision, evaluate
from app.db.models import AuditLogRow, CustomerHistoryRow
from app.schemas.event import Event, EventType
from app.services.comms.email import send_email
from app.services.comms.sms import send_sms
from app.services.comms.templates import render
from app.services.comms.templates.payment_link import (
    PAYMENT_LINK_SMS,
    PAYMENT_LINK_WHATSAPP,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "synthetic"
TEST_DB_NAME = "revenue_recovery_phase2_test"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = BACKEND_DIR / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    pytest.fail("DATABASE_URL not found")


@pytest.fixture(scope="session")
def phase2_engine():
    base_url = make_url(_database_url())
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()

    db_url = base_url.set(database=TEST_DB_NAME)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    config.set_main_option(
        "sqlalchemy.url",
        db_url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.upgrade(config, "head")
    engine = create_engine(db_url)
    yield engine
    engine.dispose()


def _payment_events() -> list[Event]:
    raw = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
    return [
        Event.model_validate(item)
        for item in raw
        if item["event_type"] == EventType.payment_failed.value
    ]


def _history(
    customer_id: str,
    *,
    contact_count: int = 0,
    last_contacted_at: datetime | None = None,
) -> CustomerHistoryRow:
    return CustomerHistoryRow(
        customer_id=customer_id,
        event_type=EventType.payment_failed.value,
        last_contacted_at=last_contacted_at,
        contact_count_7d=contact_count,
        past_failures=0,
        preferred_channel="sms",
    )


def _run_payment(event: Event, engine, history: CustomerHistoryRow | None = None) -> dict:
    with Session(engine) as db:
        if history is not None:
            db.merge(history)
            db.commit()
        return run_payment_event(event, db)


def _event(event_id: str, reason_code: str | None, amount: float = 499.0) -> Event:
    return Event(
        event_id=event_id,
        event_type=EventType.payment_failed,
        customer_id=f"cus_{event_id}",
        amount=amount,
        currency="INR",
        reason_code=reason_code,
        timestamp=datetime.now(timezone.utc),
        metadata={"customer_name": "Phase Two Tester", "source": "phase2_test"},
    )


def test_pipeline_completeness_and_audit_integrity(phase2_engine):
    events = _payment_events()[:30]
    assert len(events) == 30
    for event in events:
        state = _run_payment(event, phase2_engine)
        assert state["response"].status == "processed"
        assert len(state["response"].stage_trace) == 6

    with Session(phase2_engine) as db:
        rows = db.execute(
            select(AuditLogRow).where(AuditLogRow.event_id.in_([event.event_id for event in events]))
        ).scalars().all()
    assert len(rows) == 30
    assert {row.event_id for row in rows} == {event.event_id for event in events}
    assert all(row.action_taken and row.reasoning for row in rows)


@pytest.mark.parametrize(
    "reason_code,expected_cause",
    [(code, cause) for code, cause in CAUSE_BY_REASON_CODE.items()],
)
def test_classification_rule_mapping(reason_code, expected_cause):
    event = _event(f"classification_{reason_code}", reason_code)
    result = classify_payment({"event": event, "stage_trace": []})
    assert result["diagnosis"].cause == expected_cause
    assert reason_code in result["diagnosis"].reasoning


def test_ambiguous_classification_is_conservative_and_reviewable():
    samples = []
    for index in range(5):
        event = _event(f"ambiguous_{index}", f"unknown_code_{index}")
        diagnosis = classify_payment({"event": event, "stage_trace": []})["diagnosis"]
        samples.append(diagnosis)
    assert all(diagnosis.cause == "ambiguous" for diagnosis in samples)
    assert all("Ambiguous payment signal" in diagnosis.reasoning for diagnosis in samples)
    for diagnosis in samples:
        print(diagnosis.reasoning)


def test_gate_blocks_retry_count_and_cooldown_and_allows_safe_cases(phase2_engine):
    now = datetime.now(timezone.utc)
    blocked_retry = _event("gate_retry_block", "insufficient_funds")
    blocked_cooldown = _event("gate_cooldown_block", "processor_error")
    allowed_count = _event("gate_count_allowed", "insufficient_funds")
    allowed_cooldown = _event("gate_cooldown_allowed", "processor_error")

    blocked_states = [
        _run_payment(blocked_retry, phase2_engine, _history(blocked_retry.customer_id, contact_count=3)),
        _run_payment(
            blocked_cooldown,
            phase2_engine,
            _history(
                blocked_cooldown.customer_id,
                last_contacted_at=now - timedelta(hours=2),
            ),
        ),
    ]
    allowed_states = [
        _run_payment(allowed_count, phase2_engine, _history(allowed_count.customer_id, contact_count=2)),
        _run_payment(
            allowed_cooldown,
            phase2_engine,
            _history(
                allowed_cooldown.customer_id,
                last_contacted_at=now - timedelta(hours=48),
            ),
        ),
    ]

    for state in blocked_states:
        assert state["response"].gate_allowed is False
        assert state["response"].action == "hold"
        assert "blocked" in state["response"].gate_reason
    for state in allowed_states:
        assert state["response"].gate_allowed is True

    with Session(phase2_engine) as db:
        rows = db.execute(
            select(AuditLogRow).where(
                AuditLogRow.event_id.in_([blocked_retry.event_id, blocked_cooldown.event_id])
            )
        ).scalars().all()
    assert len(rows) == 2
    assert all("blocked" in row.reasoning for row in rows)


def test_gate_is_deterministic_for_mapping_input():
    now = datetime.now(timezone.utc)
    assert evaluate({"contact_count_7d": 3}, now).allowed is False
    assert evaluate({"contact_count_7d": 0, "last_contacted_at": now - timedelta(hours=25)}, now).allowed


@pytest.mark.parametrize(
    "cause,expected_action",
    list(ACTION_BY_CAUSE.items()),
)
def test_decision_mapping(cause, expected_action):
    result = decide_payment(
        {
            "diagnosis": PaymentDiagnosis(cause=cause, reasoning="test diagnosis"),
            "gate": GateDecision(allowed=True, reason="allowed"),
            "stage_trace": [],
        }
    )
    assert result["decision"].action == expected_action


def test_blocked_decision_is_hold():
    result = decide_payment(
        {
            "diagnosis": PaymentDiagnosis(cause="expired_card", reasoning="test diagnosis"),
            "gate": GateDecision(allowed=False, reason="blocked: cooldown is active"),
            "stage_trace": [],
        }
    )
    assert result["decision"] == PaymentDecision(
        action="hold",
        reasoning="Decision held because the risk gate blocked the event: blocked: cooldown is active",
    )


def test_template_rendering_and_missing_placeholder():
    values = {"customer_name": "Arjun", "amount": "INR 499.00", "link": "https://example.test/pay"}
    for template in (PAYMENT_LINK_SMS, PAYMENT_LINK_WHATSAPP):
        rendered = render(template, **values)
        assert rendered.channel == template.channel
        assert all(value in rendered.body for value in values.values())
    with pytest.raises(KeyError):
        render(PAYMENT_LINK_SMS, customer_name="Arjun", amount="INR 499.00")


def test_real_sms_delivery():
    result = send_sms(
        "+919631581658",
        "sms_appointment_reminders",
    )
    print(f"SMS provider={result.provider} status={result.status} id={result.provider_id}")
    assert result.success is True
    assert result.provider_id


def test_real_email_delivery():
    result = send_email(
        "arjunkumarsingh166@gmail.com",
        "Recoupe Phase 2 email delivery verification",
        "This is the real SMTP delivery verification for Recoupe Phase 2. Please check your inbox.",
    )
    print(f"Email provider={result.provider} status={result.status} id={result.provider_id}")
    assert result.success is True
    assert result.provider_id


def test_batch_metrics_output():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_batch.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Recovery rate:" in result.stdout
    assert "Recovered amount: INR" in result.stdout
