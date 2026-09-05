from app.services.comms.templates import MessageTemplate

VOICE_RENEWAL_HINGLISH = MessageTemplate(
    channel="voice",
    subject=None,
    body=(
        "Namaste {customer_name}, yeh Recoupe ki taraf se call hai. "
        "Aapke {plan} subscription ka renewal complete nahi ho paya. "
        "Seva jaari rakhne ke liye kripya apni payment details update karein. "
        "Dhanyavaad!"
    ),
)