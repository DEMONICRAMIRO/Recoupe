import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agents import cart_agent as cart_agent_module
from app.agents import renewal_agent as renewal_agent_module
from app.agents.cart_agent import (
    CART_REASONS,
    CartDecision,
    CartDiagnosis,
    _heuristic_diagnosis,
    decide_cart,
    on_feedback_received,
    run_cart_event,
)
from app.agents.renewal_agent import (
    ACTION_BY_CAUSE,
    CAUSE_BY_REASON_CODE,
    RenewalDiagnosis,
    diagnose_renewal,
    run_renewal_event,
)
from app.core.llm_client import LLMUnavailable, ModelMessage
from app.core.risk_gate import GateDecision, evaluate, evaluate_call
from app.db.models import AuditLogRow, CustomerHistoryRow
from app.schemas.event import Event, EventType
from app.services.comms import SendResult
from app.services.comms.feedback import parse_feedback_reply
from app.services.comms.voice import place_voice_call

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "synthetic"
TEST_DB_NAME = "revenue_recovery_phase3_test"

VERIFIED_PHONE = "+919631581658"
VERIFIED_EMAIL = "arjunkumarsingh166@gmail.com"


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
def phase3_engine():
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


def _events_of_type(event_type: str) -> list[Event]:
    raw = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
    return [Event.model_validate(item) for item in raw if item["event_type"] == event_type]


def _cart_event(event_id: str, **meta) -> Event:
    metadata = {
        "customer_name": "Phase Three Tester",
        "cart_items": 3,
        "device": "mobile",
        "session_duration_sec": 300,
        "purchase_history_count": 4,
        "avg_order_value": 800.0,
        "price_sensitivity": "medium",
        "source": "phase3_test",
    }
    metadata.update(meta)
    return Event(
        event_id=event_id,
        event_type=EventType.cart_abandoned,
        customer_id=f"cus_{event_id}",
        amount=1999.0,
        currency="INR",
        reason_code=None,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


def _renewal_event(event_id: str, reason_code: str, **meta) -> Event:
    metadata = {
        "customer_name": "Phase Three Tester",
        "plan": "monthly",
        "attempt": 1,
        "tenure_months": 12,
        "ltv_amount": 1000.0,
        "source": "phase3_test",
    }
    metadata.update(meta)
    return Event(
        event_id=event_id,
        event_type=EventType.renewal_failed,
        customer_id=f"cus_{event_id}",
        amount=499.0,
        currency="INR",
        reason_code=reason_code,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


def _fake_llm(monkeypatch, reasons: list[str]):
    counter = {"n": 0}

    def fake_chat(prompt, max_tokens=None):
        reason = reasons[counter["n"] % len(reasons)]
        counter["n"] += 1
        return ModelMessage(
            content=json.dumps(
                {"reason": reason, "confidence": 0.8, "explanation": "deterministic test stub"}
            ),
            model="test-stub",
        )

    monkeypatch.setattr(cart_agent_module, "chat", fake_chat)


def _ok_result(provider: str, status: str = "queued") -> SendResult:
    return SendResult(success=True, provider=provider, provider_id=f"TEST_{provider}", status=status)


def test_pipeline_completeness_and_audit_integrity(phase3_engine, monkeypatch):
    _fake_llm(monkeypatch, ["price", "timeout", "distraction"])
    cart_events = _events_of_type(EventType.cart_abandoned.value)[:30]
    renewal_events = _events_of_type(EventType.renewal_failed.value)[:30]
    assert len(cart_events) == 30
    assert len(renewal_events) == 30

    with Session(phase3_engine) as db:
        for event in cart_events:
            state = run_cart_event(event, db)
            assert state["response"].status == "processed"
            assert len(state["response"].stage_trace) == 6
            assert state["response"].cause in CART_REASONS
        for event in renewal_events:
            state = run_renewal_event(event, db)
            assert state["response"].status == "processed"
            assert len(state["response"].stage_trace) == 6
            assert state["response"].cause in CAUSE_BY_REASON_CODE.values()

        all_ids = [event.event_id for event in cart_events + renewal_events]
        rows = db.execute(
            select(AuditLogRow).where(AuditLogRow.event_id.in_(all_ids))
        ).scalars().all()

    assert len(rows) == 60
    assert {row.event_id for row in rows} == set(all_ids)
    assert all(row.action_taken and row.reasoning for row in rows)


def test_cart_diagnosis_llm_and_heuristic_fallback(monkeypatch):
    event = _cart_event("diagnosis_price_llm")

    monkeypatch.setattr(
        cart_agent_module,
        "chat",
        lambda prompt, max_tokens=None: ModelMessage(
            content=json.dumps(
                {"reason": "price", "confidence": 0.9, "explanation": "cart far above average order"}
            ),
            model="test-stub",
        ),
    )
    diagnosis = cart_agent_module.diagnose_cart({"event": event, "stage_trace": []})["diagnosis"]
    assert isinstance(diagnosis, CartDiagnosis)
    assert diagnosis.reason == "price"
    assert 0.0 <= diagnosis.confidence <= 1.0
    assert diagnosis.reasoning

    def broken_chat(prompt, max_tokens=None):
        raise LLMUnavailable("gateway down (simulated)")

    monkeypatch.setattr(cart_agent_module, "chat", broken_chat)
    fallback = cart_agent_module.diagnose_cart({"event": event, "stage_trace": []})["diagnosis"]
    assert fallback.reason in CART_REASONS
    assert "Heuristic fallback" in fallback.reasoning

    monkeypatch.setattr(
        cart_agent_module,
        "chat",
        lambda prompt, max_tokens=None: ModelMessage(content="definitely not json", model="test-stub"),
    )
    unparsable = cart_agent_module.diagnose_cart({"event": event, "stage_trace": []})["diagnosis"]
    assert unparsable.reason in CART_REASONS
    assert "Heuristic fallback" in unparsable.reasoning


def test_heuristic_price_detection():
    event = _cart_event("heuristic_price", avg_order_value=800.0, price_sensitivity="high")
    diagnosis = _heuristic_diagnosis(event, None)
    assert diagnosis.reason == "price"


@pytest.mark.parametrize(
    "reason_code,expected_cause",
    [(code, cause) for code, cause in CAUSE_BY_REASON_CODE.items()],
)
def test_renewal_diagnosis_rules(reason_code, expected_cause):
    event = _renewal_event(f"renewal_{reason_code}", reason_code)
    result = diagnose_renewal({"event": event, "stage_trace": []})
    assert result["diagnosis"].cause == expected_cause
    assert reason_code in result["diagnosis"].reasoning


@pytest.mark.parametrize("cause,expected_action", list(ACTION_BY_CAUSE.items()))
def test_renewal_decision_mapping(cause, expected_action):
    state = {
        "event": _renewal_event(f"renewal_decision_{cause}", "card_expired"),
        "diagnosis": RenewalDiagnosis(cause=cause, reasoning="test"),
        "gate": GateDecision(allowed=True, reason="allowed"),
        "stage_trace": [],
    }
    result = renewal_agent_module.decide_renewal(state)
    assert result["decision"].action == expected_action


def test_call_frequency_cap_is_independent_from_message_cap(phase3_engine):
    now = datetime.now(timezone.utc)
    assert evaluate_call({"call_count_7d": 1}, now).allowed is False
    assert evaluate({"contact_count_7d": 0}, now).allowed is True

    with Session(phase3_engine) as db:
        customer_id = "cus_call_cap"
        db.add(
            AuditLogRow(
                event_id="evt_call_cap_seed",
                action_taken="voice_call",
                reasoning="seed previous call",
                timestamp=now,
            )
        )
        db.execute(text("INSERT INTO events (event_id, event_type, customer_id, amount, currency, timestamp, metadata) VALUES (:e, :t, :c, :a, :cur, :ts, '{}')"), {
            "e": "evt_call_cap_seed",
            "t": EventType.cart_abandoned.value,
            "c": customer_id,
            "a": 100.0,
            "cur": "INR",
            "ts": now,
        })
        db.commit()
        call_info = cart_agent_module._call_history(db, customer_id, now + timedelta(hours=1))
        assert call_info["call_count_7d"] >= 1
        call_gate = evaluate_call(call_info, now + timedelta(hours=1))
        assert call_gate.allowed is False
        assert "call_count_7d" in call_gate.reason
        message_gate = evaluate({"contact_count_7d": 0, "last_contacted_at": None}, now + timedelta(hours=1))
        assert message_gate.allowed is True


def test_renewal_multi_channel_simultaneous_dispatch(phase3_engine, monkeypatch):
    sms_calls = []
    email_calls = []
    whatsapp_calls = []

    monkeypatch.setattr(
        renewal_agent_module,
        "send_sms",
        lambda to, body: sms_calls.append(to) or _ok_result("twilio_sms"),
    )
    monkeypatch.setattr(
        renewal_agent_module,
        "send_email",
        lambda to, subject, body: email_calls.append(to) or _ok_result("smtp"),
    )
    monkeypatch.setattr(
        renewal_agent_module,
        "send_whatsapp",
        lambda to, body: whatsapp_calls.append(to) or _ok_result("twilio_sms", "queued_whatsapp_fallback"),
    )
    monkeypatch.setattr(
        renewal_agent_module,
        "place_voice_call",
        lambda to, script, url_params=None: _ok_result("twilio_voice"),
    )

    event = _renewal_event(
        "renewal_multi_channel",
        "card_expired",
        phone=VERIFIED_PHONE,
        email=VERIFIED_EMAIL,
        ltv_amount=1000.0,
    )
    with Session(phase3_engine) as db:
        state = run_renewal_event(event, db)

    assert state["response"].action == "prompt_card_update"
    channels = {item["channel"] for item in state["deliveries"]}
    assert channels == {"sms", "email", "whatsapp_fallback"}
    assert len(sms_calls) == 1
    assert len(email_calls) == 1
    assert len(whatsapp_calls) == 1
    assert all(item.get("result", {}).get("status") for item in state["deliveries"])


def test_feedback_loop_triggers_voice_only_for_payment_issue(phase3_engine, monkeypatch):
    _fake_llm(monkeypatch, ["timeout"])
    monkeypatch.setattr(cart_agent_module, "send_sms", lambda to, body: _ok_result("twilio_sms"))
    monkeypatch.setattr(cart_agent_module, "send_feedback_prompt", lambda to, name: _ok_result("twilio_sms"))
    monkeypatch.setattr(cart_agent_module, "send_email", lambda to, subject, body: _ok_result("smtp"))

    voice_calls = []
    monkeypatch.setattr(
        cart_agent_module,
        "place_voice_call",
        lambda to, script, url_params=None: voice_calls.append(to) or _ok_result("twilio_voice"),
    )

    event = _cart_event("cart_feedback_loop", phone=VERIFIED_PHONE)
    with Session(phase3_engine) as db:
        run_cart_event(event, db)
        assert on_feedback_received(event.event_id, "Payment issue", db)["placed"] is True
        assert on_feedback_received(event.event_id, "Just browsing", db)["placed"] is False
        rows = db.execute(
            select(AuditLogRow).where(AuditLogRow.event_id == event.event_id).order_by(AuditLogRow.id.desc())
        ).scalars().all()

    assert len(voice_calls) == 1
    assert voice_calls[0] == VERIFIED_PHONE
    assert any("feedback_reason=Just browsing" in (row.reasoning or "") for row in rows)
    assert any(row.action_taken == "voice_call" for row in rows)


def test_feedback_parsing_structured_options():
    assert parse_feedback_reply("2").canonical == "Payment issue"
    assert parse_feedback_reply("payment issue").canonical == "Payment issue"
    assert parse_feedback_reply("1").canonical == "Too expensive"
    assert parse_feedback_reply("3").canonical == "Just browsing"
    assert parse_feedback_reply("gibberish").canonical == "Other"


@pytest.mark.skip(reason="Twilio trial account voice quota exhausted/restricted")
def test_real_voice_call_delivery():
    script = (
        "Namaste, yeh Recoupe ke taraf se ek test call hai. "
        "Kripya is call ko ignore karein. Dhanyavaad!"
    )
    result = place_voice_call(VERIFIED_PHONE, script)
    print(f"Voice provider={result.provider} status={result.status} id={result.provider_id}")
    assert result.success is True
    assert result.provider_id


def test_cart_decision_mapping(monkeypatch):
    hold_state = {
        "event": _cart_event("cart_hold"),
        "diagnosis": CartDiagnosis(reason="price", confidence=0.8, reasoning="test"),
        "gate": GateDecision(allowed=False, reason="blocked: cooldown is active"),
        "stage_trace": [],
    }
    hold = decide_cart(hold_state)
    assert hold["decision"] == CartDecision(
        nudge_type="hold",
        discount_tier=None,
        reasoning="Decision held because the risk gate blocked the event: blocked: cooldown is active",
    )

    price_state = {
        "event": _cart_event("cart_price", price_sensitivity="high"),
        "diagnosis": CartDiagnosis(reason="price", confidence=0.8, reasoning="test"),
        "gate": GateDecision(allowed=True, reason="allowed"),
        "stage_trace": [],
    }
    discount = decide_cart(price_state)
    assert discount["decision"].nudge_type == "discount"
    assert discount["decision"].discount_tier == "10%"


def test_llm_uses_test_model_under_pytest(monkeypatch):
    import app.core.llm_client as llm_client_module
    from app.core.config import settings as cfg

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": cfg.test_model_name,
                "cost": "0",
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            }

    def fake_post(*args, **kwargs):
        captured["payload"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr(llm_client_module.httpx, "post", fake_post)
    message = llm_client_module.chat("ping")
    assert message.content == "OK"
    assert llm_client_module.running_under_pytest() is True
    assert llm_client_module.default_model_name() == cfg.test_model_name
    assert captured["payload"]["model"] == cfg.test_model_name
