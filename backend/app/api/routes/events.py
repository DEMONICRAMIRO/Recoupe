"""events.py — Event ingest endpoints.

Two endpoints:
  POST /events/ingest         — synthetic event ingest (existing, unchanged)
  POST /events/razorpay-webhook — real Razorpay webhook receiver (Phase 5)
  POST /events/run-batch      — trigger run_batch.py subprocess for Live run demo
"""
import hashlib
import hmac
import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.adapters.razorpay_adapter import (
    RazorpayPayloadError,
    RazorpaySignatureError,
    translate_to_event,
    verify_webhook_signature,
)
from app.agents.graph import run_event
from app.core.config import settings
from app.core.live_status import finish_batch, start_batch
from app.schemas.agent import SubAgentResponse
from app.schemas.event import Event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])

# Resolved path to run_batch.py (repo root = 4 levels above this file)
_REPO_ROOT = Path(__file__).resolve().parents[4]   # backend/app/api/routes/events.py → [4] = Recoupe
_RUN_BATCH_SCRIPT = _REPO_ROOT / "scripts" / "run_batch.py"


@router.post("/ingest", response_model=SubAgentResponse)
def ingest_event(event: Event) -> SubAgentResponse:
    """Existing synthetic event ingest — unchanged from Phases 1-4."""
    state = run_event(event)
    return state["response"]


@router.post("/razorpay-webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(None),
) -> dict:
    """Accept Razorpay webhook POST, verify signature, run through pipeline.

    Signature verification uses HMAC-SHA256 on the raw request body BEFORE
    JSON parsing, per Razorpay's documentation requirement.

    Returns 200 for all handled and unhandled events (so Razorpay doesn't retry).
    Returns 400 only for signature failures.
    """
    raw_body = await request.body()

    # --- Signature verification ---
    webhook_secret = settings.razorpay_webhook_secret
    if webhook_secret and x_razorpay_signature:
        try:
            verify_webhook_signature(raw_body, x_razorpay_signature, webhook_secret)
        except RazorpaySignatureError as exc:
            logger.warning("Razorpay webhook signature failure: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook signature verification failed",
            ) from exc
    elif webhook_secret and not x_razorpay_signature:
        logger.warning("Razorpay webhook missing X-Razorpay-Signature header; rejecting")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Razorpay-Signature header",
        )
    else:
        # No secret configured — allow (useful for local testing without ngrok)
        logger.debug("razorpay_webhook: no webhook secret configured, skipping signature check")

    # --- Parse and translate ---
    import json
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Razorpay webhook: invalid JSON body: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    event_type = payload.get("event", "unknown")
    logger.info("razorpay_webhook: received event_type=%r", event_type)

    try:
        event = translate_to_event(payload)
    except RazorpayPayloadError as exc:
        logger.error("razorpay_webhook: payload translation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if event is None:
        # Unhandled event type — acknowledge and ignore
        return {
            "status": "ignored",
            "event_type": event_type,
            "message": "Event type not handled by Recoupe pipeline",
        }

    # --- Run through the pipeline (same path as synthetic events) ---
    try:
        state = run_event(event)
        response: SubAgentResponse = state["response"]
        logger.info(
            "razorpay_webhook: processed %s → action=%s",
            event.event_id,
            response.action,
        )
        return {
            "status": "processed",
            "event_id": event.event_id,
            "agent": response.agent,
            "action": response.action,
        }
    except Exception as exc:
        logger.exception("razorpay_webhook: pipeline error for %s: %s", event.event_id, exc)
        # Still return 200 to avoid Razorpay retrying; log the failure
        return {
            "status": "pipeline_error",
            "event_id": event.event_id,
            "error": str(exc),
        }


def _run_batch_background() -> None:
    """Subprocess runner for the Live run view's 'Run batch' button."""
    start_batch()
    try:
        subprocess.run(
            [sys.executable, str(_RUN_BATCH_SCRIPT)],
            check=False,
            capture_output=False,
        )
    finally:
        finish_batch()


@router.post("/run-batch")
def run_batch(background_tasks: BackgroundTasks) -> dict:
    """Trigger run_batch.py as a background subprocess for the Live run view.

    Returns immediately; the batch runs asynchronously. The frontend polls
    GET /live-status to watch progress.
    """
    if not _RUN_BATCH_SCRIPT.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"run_batch.py not found at {_RUN_BATCH_SCRIPT}",
        )
    start_batch()
    background_tasks.add_task(_run_batch_background)
    return {"status": "started", "message": "Batch started. Poll /live-status to watch progress."}
