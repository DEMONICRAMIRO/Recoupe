"""batch_runner.py — in-process synthetic batch runner for the Live run demo.

Runs the bundled demo event set through the agent pipeline in the SAME
process as the FastAPI app. This matters because each agent calls
app.core.live_status.update_live_status() as it works — that store is a
plain in-memory module dict, so those updates are only visible to
GET /live-status (polled by the dashboard) if the batch runs in this
process rather than a spawned subprocess.

EVENTS_PATH points at a static snapshot checked into the app package
(backend/app/data/synthetic/events.json), not the dev-only regeneratable
data/synthetic/events.json at the repo root — that file is gitignored and
sits outside backend/, so it does not ship with a deploy whose build root
is the backend/ directory. Regenerate this snapshot manually (copy the
output of data/synthetic/generate.py here) if the demo dataset needs to
change.
"""
import logging
from collections import Counter
from pathlib import Path

from app.adapters.synthetic_adapter import load_events
from app.agents.graph import run_event

logger = logging.getLogger(__name__)

EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "events.json"

# Actions that represent a "recovery attempt" per agent type
RECOVERY_ACTIONS = {
    "payment_agent": {"retry_now", "retry_alt_route", "send_payment_link"},
    "cart_agent": {"nudge", "discount_nudge"},
    "renewal_agent": {"prompt_card_update", "grace_period", "silent_retry"},
    "invoice_agent": {"send_reminder", "send_firm_followup", "escalate_with_call", "escalate_no_call"},
}

# State keys to extract agent-specific response
RESULT_KEYS = {
    "payment_agent": "payment_result",
    "cart_agent": "cart_result",
    "renewal_agent": "renewal_result",
    "invoice_agent": "invoice_result",
}


def run_synthetic_batch(events_path: Path | None = None) -> dict:
    """Run the bundled synthetic events through the pipeline in-process.

    Returns aggregate + per-agent recovery stats. Mirrors the CLI logic in
    scripts/run_batch.py, which stays independent for local/dev use against
    the regeneratable repo-root dataset.
    """
    path = events_path or EVENTS_PATH
    events = load_events(path)

    counts: Counter = Counter()
    by_agent: dict[str, dict] = {
        name: {"total": 0, "recovered": 0, "amount": 0.0} for name in RECOVERY_ACTIONS
    }

    for event in events:
        state = run_event(event)
        # Try agent-specific result key first, fall back to generic response
        agent_name = None
        response = None
        for name, key in RESULT_KEYS.items():
            if key in state and state[key] is not None:
                response = state[key]
                agent_name = name
                break
        if response is None:
            response = state["response"]
            agent_name = response.agent

        counts[agent_name] += 1
        if agent_name in by_agent:
            by_agent[agent_name]["total"] += 1
            if response.action in RECOVERY_ACTIONS.get(agent_name, set()):
                by_agent[agent_name]["recovered"] += 1
                by_agent[agent_name]["amount"] += event.amount

    total_events = len(events)
    total_recovered = sum(v["recovered"] for v in by_agent.values())
    total_amount = sum(v["amount"] for v in by_agent.values())
    overall_rate = (total_recovered / total_events * 100) if total_events else 0.0

    return {
        "total_events": total_events,
        "counts": dict(counts),
        "by_agent": by_agent,
        "total_recovered": total_recovered,
        "total_amount": total_amount,
        "recovery_rate_pct": overall_rate,
    }
