from app.services.comms.templates import MessageTemplate

CART_NUDGE_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, you left {cart_items} item(s) in your cart. "
        "They are still waiting for you — tap to finish checkout: {link}"
    ),
)

CART_NUDGE_EMAIL = MessageTemplate(
    channel="email",
    subject="You left something behind",
    body=(
        "Hi {customer_name},\n\n"
        "Your cart with {cart_items} item(s) is still waiting for you. "
        "Finish your checkout here: {link}\n\n"
        "— Recoupe"
    ),
)

CART_DISCOUNT_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, your cart is waiting — here is {discount} off "
        "to help you decide. Checkout link: {link}"
    ),
)

CART_DISCOUNT_EMAIL = MessageTemplate(
    channel="email",
    subject="Your cart + {discount} off inside",
    body=(
        "Hi {customer_name},\n\n"
        "We saved your cart. Use this checkout link for {discount} off: {link}\n\n"
        "— Recoupe"
    ),
)