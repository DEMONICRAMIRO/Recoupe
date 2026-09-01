import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.synthetic_adapter import load_events
from app.agents.graph import run_event


def main() -> None:
    events_path = REPO_ROOT / "data" / "synthetic" / "events.json"
    events = load_events(events_path)
    counts: Counter = Counter()
    payment_events = 0
    recovery_candidates = 0
    recovered_amount = 0.0
    for event in events:
        state = run_event(event)
        response = state.get("payment_result", state["response"])
        counts[response.agent] += 1
        if response.agent == "payment_agent":
            payment_events += 1
            if response.action in {"retry_now", "retry_alt_route", "send_payment_link"}:
                recovery_candidates += 1
                recovered_amount += event.amount
    print(f"Processed {len(events)} events")
    for agent, count in sorted(counts.items()):
        print(f"  {agent}: {count}")
    recovery_rate = (recovery_candidates / payment_events * 100) if payment_events else 0.0
    print(f"Recovery rate: {recovery_rate:.1f}%")
    print(f"Recovered amount: INR {recovered_amount:.2f}")


if __name__ == "__main__":
    main()
