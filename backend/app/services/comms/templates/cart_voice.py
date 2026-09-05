from app.services.comms.templates import MessageTemplate

VOICE_CART_HINGLISH = MessageTemplate(
    channel="voice",
    subject=None,
    body=(
        "Namaste {customer_name}, yeh Recoupe ki taraf se call hai. "
        "Aapka cart abhi bhi saved hai aur hum madad karna chahte hain. "
        "Agar payment mein koi problem ho rahi hai, toh kripya humein batayein. "
        "Thank you!"
    ),
)