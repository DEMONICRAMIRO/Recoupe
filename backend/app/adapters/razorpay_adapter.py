"""razorpay_adapter.py — Translates Razorpay webhook payloads into the internal Event schema.

Per architecture.md Section 6 and Section 10:
  - Nothing downstream ever sees a raw external payload.
  - There is exactly ONE internal Event schema; adapters translate into it.
  - The resulting Event is identical to a synthetic Event and flows through the
    same Orchestrator -> Payment Agent pipeline without forking.

Razorpay payload shape (payment.failed):
  {
    "account_id": "acc_...",
    "event": "payment.failed",
    "created_at": <unix_epoch>,
    "payload": {
      "payment": {
        "entity": {
          "id": "pay_...",           -> event_id (prefixed rzp_)
          "amount": 50000,           -> amount / 100 (paise -> INR)
          "currency": "INR",         -> currency
          "status": "failed",
          "order_id": "order_...",
          "method": "card|upi|...",
          "error_code": "...",       -> reason_code
          "error_description": "...",
          "error_reason": "...",
          "customer_id": "cust_...", -> customer_id (may be null)
          "contact": "+91...",       -> metadata.phone (fallback customer_id source)
          "email": "...",            -> metadata.email
          "notes": {...},            -> merged into metadata
          "created_at": <epoch>      -> timestamp
        }
      }
    }
  }

Webhook signature verification:
  X-Razorpay-Signature = HMAC-SHA256(raw_body, webhook_secret)
  Must verify BEFORE parsing JSON, using the raw bytes.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from app.schemas.event import Event, EventType

logger = logging.getLogger(__name__)

# Supported Razorpay event types we handle
HANDLED_EVENTS = {"payment.failed"}


class RazorpaySignatureError(Exception):
    """Raised when webhook HMAC signature verification fails."""


class RazorpayPayloadError(Exception):
    """Raised when a webhook payload is missing required fields."""


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> None:
    """Verify the X-Razorpay-Signature HMAC-SHA256 header.

    Raises RazorpaySignatureError if verification fails.
    Must be called on the raw bytes BEFORE JSON parsing.
    """
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise RazorpaySignatureError(
            "Webhook signature mismatch — possible replay or tampered payload"
        )


def _epoch_to_utc(epoch: int | float | None) -> datetime:
    """Convert a Unix epoch integer to a UTC-aware datetime."""
    if epoch is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


def _extract_customer_id(entity: dict) -> str:
    """Derive a stable customer_id from the payment entity.

    Preference order:
      1. entity.customer_id  (set when Razorpay Customers API was used)
      2. entity.contact      (phone number — reliable in test mode)
      3. entity.email
      4. entity.order_id     (last resort — at least ties back to an order)
      5. entity.id           (payment ID itself — never null)
    """
    for field in ("customer_id", "contact", "email", "order_id"):
        val = entity.get(field)
        if val:
            return str(val)
    return str(entity["id"])


def translate_payment_failed(entity: dict, event_created_at: int | None = None) -> Event:
    """Translate a Razorpay payment entity dict into an internal Event.

    Args:
        entity: The `payload.payment.entity` dict from a Razorpay webhook.
        event_created_at: Top-level `created_at` from the webhook envelope (epoch int).
                          Used as a fallback timestamp if entity.created_at is absent.

    Returns:
        An Event with event_type=payment_failed, ready for the Orchestrator pipeline.
    """
    if "id" not in entity:
        raise RazorpayPayloadError("payment entity missing required 'id' field")

    payment_id = entity["id"]
    # Prefix to make it trivially distinguishable from synthetic IDs in the DB
    event_id = f"rzp_{payment_id}"

    # Amount: Razorpay stores in smallest currency unit (paise for INR)
    raw_amount = entity.get("amount", 0)
    amount_inr = float(raw_amount) / 100.0

    currency = str(entity.get("currency", "INR"))
    reason_code = entity.get("error_code") or entity.get("error_reason")
    customer_id = _extract_customer_id(entity)

    # Timestamp: prefer entity.created_at, fall back to envelope created_at
    ts_epoch = entity.get("created_at") or event_created_at
    timestamp = _epoch_to_utc(ts_epoch)

    # Metadata: everything useful for the Payment Agent's Enrich + Classify steps
    notes = entity.get("notes") or {}
    metadata = {
        "customer_name": (
            notes.get("customer_name")
            or entity.get("email")
            or customer_id
        ),
        "payment_method": entity.get("method", "unknown"),
        "phone": entity.get("contact"),
        "email": entity.get("email"),
        "order_id": entity.get("order_id"),
        "razorpay_payment_id": payment_id,
        "error_description": entity.get("error_description"),
        "error_reason": entity.get("error_reason"),
        "payment_link": f"https://rzp.io/i/recovery-demo/{payment_id}",
        **{k: v for k, v in notes.items() if k not in ("customer_name",)},
    }

    return Event(
        event_id=event_id,
        event_type=EventType.payment_failed,
        customer_id=customer_id,
        amount=amount_inr,
        currency=currency,
        reason_code=reason_code,
        timestamp=timestamp,
        metadata=metadata,
    )


def translate_to_event(payload: dict) -> Event | None:
    """Translate a full Razorpay webhook payload dict into an internal Event.

    Args:
        payload: The parsed JSON body of a Razorpay webhook POST.

    Returns:
        An Event if the webhook type is handled, None if it should be acknowledged
        but not processed (e.g. payment.captured, order.paid, etc.).

    Raises:
        RazorpayPayloadError if the payload is malformed for a handled event type.
    """
    event_type = payload.get("event", "")
    if event_type not in HANDLED_EVENTS:
        logger.info("razorpay_adapter: ignoring unhandled event type %r", event_type)
        return None

    event_created_at = payload.get("created_at")

    try:
        entity = payload["payload"]["payment"]["entity"]
    except (KeyError, TypeError) as exc:
        raise RazorpayPayloadError(
            f"Malformed payment.failed payload — missing path payload.payment.entity: {exc}"
        ) from exc

    if event_type == "payment.failed":
        return translate_payment_failed(entity, event_created_at)

    return None  # unreachable given HANDLED_EVENTS check, but satisfies type checker
