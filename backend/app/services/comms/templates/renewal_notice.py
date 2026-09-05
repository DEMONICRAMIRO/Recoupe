from app.services.comms.templates import MessageTemplate

RENEWAL_EXPIRY_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, we could not renew your {plan} subscription. "
        "Update your payment details to avoid interruption: {link}"
    ),
)

RENEWAL_EXPIRY_EMAIL = MessageTemplate(
    channel="email",
    subject="Your subscription renewal needs attention",
    body=(
        "Hi {customer_name},\n\n"
        "We were unable to renew your {plan} subscription. "
        "Please update your payment details here: {link}\n\n"
        "— Recoupe"
    ),
)