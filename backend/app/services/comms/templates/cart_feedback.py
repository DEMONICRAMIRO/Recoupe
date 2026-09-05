from app.services.comms.templates import MessageTemplate

CART_FEEDBACK_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, quick question about your abandoned cart — "
        "reply with: 1 Too expensive, 2 Payment issue, 3 Just browsing, 4 Other"
    ),
)