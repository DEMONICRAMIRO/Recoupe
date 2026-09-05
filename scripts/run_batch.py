import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.synthetic_adapter import load_events
from app.agents.graph import run_event

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


def main() -> None:
    events_path = REPO_ROOT / "data" / "synthetic" / "events.json"
    events = load_events(events_path)
    counts: Counter = Counter()
    by_agent: dict[str, dict] = {
        name: {"total": 0, "recovered": 0, "amount": 0.0}
        for name in RECOVERY_ACTIONS
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

    print(f"Processed {total_events} events")
    for agent, count in sorted(counts.items()):
        print(f"  {agent}: {count}")

    print()
    print("Per-agent recovery:")
    for name, stats in sorted(by_agent.items()):
        rate = (stats["recovered"] / stats["total"] * 100) if stats["total"] else 0.0
        print(f"  {name}: {stats['recovered']}/{stats['total']} recovered ({rate:.1f}%)"
              f"  INR {stats['amount']:,.2f}")

    overall_rate = (total_recovered / total_events * 100) if total_events else 0.0
    print()
    print(f"Recovery rate: {overall_rate:.1f}%")
    print(f"Recovered amount: INR {total_amount:,.2f}")


if __name__ == "__main__":
    main()
