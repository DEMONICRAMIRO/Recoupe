"""live_status route — GET /live-status polling endpoint for the dashboard."""
from fastapi import APIRouter

from app.core.live_status import get_live_status, get_stats

router = APIRouter(prefix="/live-status", tags=["live-status"])


@router.get("/")
def live_status() -> dict:
    """Return current in-flight event states, or last batch if nothing is running.

    Frontend polls this every 1-2 seconds while watching the Live run view.
    """
    entries = get_live_status()
    stats = get_stats()
    return {
        "events": entries,
        "stats": stats,
    }
