from app.services.comms.templates import MessageTemplate


PAYMENT_LINK_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, your payment of {amount} could not be completed. "
        "Securely complete it here: {link}"
    ),
)

PAYMENT_LINK_WHATSAPP = MessageTemplate(
    channel="whatsapp",
    subject=None,
    body=(
        "Hi {customer_name}, we could not complete your payment of {amount}. "
        "You can securely retry it here: {link}"
    ),
)
