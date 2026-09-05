"""comms.py — Comms channel status endpoint.

CRITICAL: This endpoint MUST reflect the actual current state of each channel,
matching architecture.md Section 3b. It must NOT claim WhatsApp delivery is live
when it is not. Criterion 7 of test_phase5.py tests this explicitly.

Current state as of Phase 4 close:
  - Email: LIVE (SMTP/SendGrid confirmed working in Phase 2)
  - SMS: LIVE (Twilio test-mode confirmed working in Phase 2/3)
  - WhatsApp: BUILT but delivery BLOCKED on this Twilio account.
              send_whatsapp() is implemented and falls back to SMS.
              Do not claim "live" here.
  - Voice: Infrastructure confirmed (Twilio call placement API returns 200,
           TwiML Bin configured), but audio-level verification inconclusive
           due to carrier intercept on the trial number. Not claiming full "live".
"""
from fastapi import APIRouter

router = APIRouter(prefix="/comms", tags=["comms"])


@router.get("/status")
def comms_status() -> dict:
    """Return honest delivery status for all four comms channels."""
    return {
        "channels": [
            {
                "name": "Email",
                "status": "live",
                "status_label": "Live",
                "description": "SMTP/transactional email via configured SMTP relay. Delivery confirmed end-to-end.",
                "provider": "SMTP",
                "used_by": ["payment_agent", "cart_agent", "renewal_agent", "invoice_agent"],
                "meta": "send_email() in services/comms/email.py",
            },
            {
                "name": "SMS",
                "status": "live",
                "status_label": "Live",
                "description": "Twilio test-mode SMS. Delivery confirmed to verified trial number.",
                "provider": "Twilio",
                "used_by": ["payment_agent", "cart_agent", "renewal_agent", "invoice_agent"],
                "meta": "send_sms() in services/comms/sms.py — body argument (not content_sid)",
            },
            {
                "name": "WhatsApp",
                # MUST NOT be "live" — this is tested by Criterion 7
                "status": "blocked",
                "status_label": "Sender built, delivery blocked",
                "description": (
                    "Twilio WhatsApp sandbox sender is implemented and wired. "
                    "Business-initiated delivery is currently blocked on this Twilio account. "
                    "All WhatsApp channel calls fall back to SMS in practice. "
                    "Update this status the moment the block clears."
                ),
                "provider": "Twilio WhatsApp",
                "used_by": ["payment_agent", "cart_agent", "renewal_agent", "invoice_agent"],
                "meta": "send_whatsapp() in services/comms/whatsapp.py — falls back to SMS",
            },
            {
                "name": "Voice",
                "status": "partial",
                "status_label": "Infrastructure confirmed, audio inconclusive",
                "description": (
                    "Twilio Voice call placement confirmed at API level (HTTP 200, call SID returned). "
                    "TwiML Bin configured with Hinglish script. "
                    "Audio-level verification remains inconclusive due to carrier intercept "
                    "on the Twilio trial number. Not claiming full live delivery."
                ),
                "provider": "Twilio Voice + TwiML",
                "used_by": ["cart_agent", "renewal_agent", "invoice_agent"],
                "meta": "place_voice_call() in services/comms/voice.py",
            },
        ]
    }
