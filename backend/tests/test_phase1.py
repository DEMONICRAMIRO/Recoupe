import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.adapters.synthetic_adapter import load_events
from app.agents.graph import run_event
from app.main import app
from app.schemas.agent import PLACEHOLDER_ACTION, STUB_STATUS, SubAgentResponse
from app.schemas.event import Event, EventType

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "synthetic"
GENERATE_SCRIPT = DATA_DIR / "generate.py"
EVENTS_JSON = DATA_DIR / "events.json"
HISTORY_JSON = DATA_DIR / "customer_history.json"

EVENT_TYPE_NAMES = [t.value for t in EventType]
AGENT_BY_TYPE = {
    "payment_failed": "payment_agent",
    "cart_abandoned": "cart_agent",
    "renewal_failed": "renewal_agent",
    "invoice_overdue": "invoice_agent",
}
REQUIRED_EVENT_FIELDS = ("event_id", "event_type", "customer_id", "amount", "currency", "timestamp", "metadata")
TEST_DB_NAME = "revenue_recovery_test"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    pytest.fail("DATABASE_URL not found: set the env var or create backend/.env")


@pytest.fixture(scope="session")
def migrated_db_url():
    base_url = make_url(_database_url())
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()

    scratch = base_url.set(database=TEST_DB_NAME)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", scratch.render_as_string(hide_password=False).replace("%", "%%"))
    command.upgrade(cfg, "head")
    return scratch


@pytest.fixture(scope="session")
def generated_data():
    subprocess.run(
        [sys.executable, str(GENERATE_SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
    )
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    return events, history


def _table_columns(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_health_endpoint_returns_200():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("status") == "ok"


def test_ingest_endpoint_routes_event_to_correct_stub():
    event = Event(
        event_id="evt_api_payment_1",
        event_type=EventType.payment_failed,
        customer_id="cus_api_1",
        amount=499.0,
        currency="INR",
        reason_code="insufficient_funds",
        timestamp=datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc),
        metadata={"source": "api_test", "attempt": 1},
    )
    with TestClient(app) as client:
        response = client.post("/events/ingest", json=event.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "payment_agent"
    assert body["event_id"] == event.event_id
    assert body["action"] == PLACEHOLDER_ACTION


def test_migration_applies_cleanly_on_fresh_db(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"events", "customer_history", "audit_log"}.issubset(tables)


def test_events_table_matches_event_schema(migrated_db_url):
    assert _table_columns(migrated_db_url, "events") == set(Event.model_fields)


def test_customer_history_table_columns(migrated_db_url):
    expected = {
        "customer_id",
        "event_type",
        "last_contacted_at",
        "contact_count_7d",
        "past_failures",
        "preferred_channel",
    }
    assert _table_columns(migrated_db_url, "customer_history") == expected


def test_audit_log_table_columns(migrated_db_url):
    expected = {"id", "event_id", "action_taken", "reasoning", "timestamp"}
    assert _table_columns(migrated_db_url, "audit_log") == expected


def test_generator_volume(generated_data):
    events, _ = generated_data
    assert len(events) >= 100


def test_generator_type_coverage(generated_data):
    events, _ = generated_data
    counts = Counter(event["event_type"] for event in events)
    for event_type in EVENT_TYPE_NAMES:
        assert counts[event_type] >= 20, f"{event_type} has only {counts[event_type]} events"


def test_generator_no_null_required_fields(generated_data):
    events, _ = generated_data
    for event in events:
        for field in REQUIRED_EVENT_FIELDS:
            assert event.get(field) is not None, f"{event.get('event_id')} has null {field}"
        assert isinstance(event["metadata"], dict)


def test_customer_history_covers_all_customers(generated_data):
    events, history = generated_data
    history_ids = {row["customer_id"] for row in history}
    event_ids = {event["customer_id"] for event in events}
    assert event_ids == history_ids


def test_generated_events_conform_to_event_schema(generated_data):
    events, _ = generated_data
    parsed = load_events(EVENTS_JSON)
    assert len(parsed) == len(events)
    assert all(isinstance(event, Event) for event in parsed)
    assert all(event.event_type in EventType for event in parsed)


def _hand_picked_events():
    base = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    reason_codes = {
        "payment_failed": ["insufficient_funds", "card_expired", None, "auth_declined", "processor_error"],
        "cart_abandoned": [None, None, None, None, None],
        "renewal_failed": ["card_expired", None, "mandate_revoked", "insufficient_funds", "processor_error"],
        "invoice_overdue": [None, None, None, None, None],
    }
    params = []
    for i, event_type in enumerate(EVENT_TYPE_NAMES):
        for j, reason_code in enumerate(reason_codes[event_type]):
            event = Event(
                event_id=f"evt_handpicked_{event_type}_{j}",
                event_type=event_type,
                customer_id=f"cus_handpicked_{event_type}_{j}",
                amount=round(100.0 + i * 10 + j * 1.5, 2),
                currency="INR",
                reason_code=reason_code,
                timestamp=base + timedelta(minutes=i * 10 + j),
                metadata={"source": "hand_picked", "variant": j},
            )
            params.append(pytest.param(event, AGENT_BY_TYPE[event_type], id=f"{event_type}-{j}"))
    return params


@pytest.mark.parametrize("event,expected_agent", _hand_picked_events())
def test_routing_correctness(event, expected_agent):
    state = run_event(event)
    assert state["routed_to"] == expected_agent
    assert state["response"].agent == expected_agent


@pytest.mark.parametrize("event,expected_agent", _hand_picked_events())
def test_stub_response_shape(event, expected_agent):
    state = run_event(event)
    response = state["response"]
    assert isinstance(response, SubAgentResponse)
    round_trip = SubAgentResponse.model_validate(response.model_dump())
    assert round_trip == response
    assert response.agent == expected_agent
    assert response.event_id == event.event_id
    assert response.event_type == event.event_type.value
    assert response.action == PLACEHOLDER_ACTION
    assert response.status == STUB_STATUS
    assert response.reasoning
