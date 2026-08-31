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
    for event in events:
        state = run_event(event)
        counts[state["response"].agent] += 1
    print(f"Processed {len(events)} events")
    for agent, count in sorted(counts.items()):
        print(f"  {agent}: {count}")


if __name__ == "__main__":
    main()
