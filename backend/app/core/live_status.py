"""live_status.py — In-memory live-run state store for the dashboard.

Design principles (per PRD Section 4c):
  - Simple in-memory dict keyed by event_id — no DB, no message queue.
  - Thread-safe: all mutations behind a single threading.Lock.
  - Each agent pipeline calls update_live_status() at the START of each stage.
  - The dashboard frontend polls GET /live-status every 1-2 seconds.
  - If no batch is currently running, the last completed batch's final states are
    returned (so the dashboard is never blank after a run completes).

Stage names (matches the six-stage pipeline in architecture.md Section 4):
  "fetch" -> "enrich" -> "classify" -> "gate" -> "decide" -> "execute_log"
"""

import threading
from datetime import datetime, timezone

_lock = threading.Lock()

# Active batch: event_id -> status dict
_live: dict[str, dict] = {}

# Last completed batch snapshot (shown when nothing is in flight)
_last_batch: dict[str, dict] = {}

# Ordered stage list for the six-stage pipeline
PIPELINE_STAGES = ["fetch", "enrich", "classify", "gate", "decide", "execute_log"]

_TERMINAL_STAGES = {"execute_log"}


def update_live_status(
    event_id: str,
    stage: str,
    *,
    agent: str | None = None,
    action: str | None = None,
) -> None:
    """Called by each agent at the START of each pipeline stage.

    Args:
        event_id: Unique event identifier.
        stage:    One of the PIPELINE_STAGES values.
        agent:    Agent name (e.g. "payment_agent"), set on first call per event.
        action:   Final action taken, set on execute_log call.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        existing = _live.get(event_id, {})
        entry = {
            "event_id": event_id,
            "agent": agent or existing.get("agent"),
            "stage": stage,
            "action": action or existing.get("action"),
            "started_at": existing.get("started_at", now),
            "updated_at": now,
            "complete": stage in _TERMINAL_STAGES,
            "stages_done": _stages_done(existing.get("stages_done", []), stage),
        }
        _live[event_id] = entry


def _stages_done(prev: list[str], current: str) -> list[str]:
    """Accumulate completed stages in pipeline order."""
    result = list(prev)
    if current not in result:
        result.append(current)
    return result


def get_live_status() -> list[dict]:
    """Return current in-flight state, or last batch if nothing is running.

    Returns a list sorted by started_at descending (most recent first).
    """
    with _lock:
        active = dict(_live)

    if active:
        source = active
    else:
        with _lock:
            source = dict(_last_batch)

    return sorted(source.values(), key=lambda e: e.get("started_at", ""), reverse=True)


def start_batch() -> None:
    """Called at the start of a new batch run to clear the in-flight state."""
    with _lock:
        _live.clear()


def finish_batch() -> None:
    """Called when a batch run completes. Saves final state for dashboard display."""
    with _lock:
        global _last_batch
        _last_batch = dict(_live)
        _live.clear()


def get_stats() -> dict:
    """Return summary counts for the current or last batch."""
    with _lock:
        source = _live if _live else _last_batch

    total = len(source)
    complete = sum(1 for e in source.values() if e.get("complete"))
    in_flight = total - complete
    by_agent: dict[str, int] = {}
    for e in source.values():
        a = e.get("agent") or "unknown"
        by_agent[a] = by_agent.get(a, 0) + 1

    return {
        "total": total,
        "complete": complete,
        "in_flight": in_flight,
        "by_agent": by_agent,
    }
