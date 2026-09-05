"""invoice_reminder.py — Templates for Invoice Agent communications.

Follows the Section 3a convention: one file per scenario, one MessageTemplate per channel.
Placeholders: customer_name, invoice_number, amount, days_overdue, due_date, link.
"""

from app.services.comms.templates import MessageTemplate

# --- Reminder (30/60-day overdue: first formal contact) ---

INVOICE_REMINDER_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Hi {customer_name}, invoice {invoice_number} for INR {amount} "
        "is {days_overdue} days overdue (due {due_date}). "
        "Please settle at: {link}"
    ),
)

INVOICE_REMINDER_EMAIL = MessageTemplate(
    channel="email",
    subject="Invoice {invoice_number} — Payment Overdue by {days_overdue} Days",
    body=(
        "Hi {customer_name},\n\n"
        "This is a reminder that invoice {invoice_number} for INR {amount} "
        "was due on {due_date} and is now {days_overdue} days overdue.\n\n"
        "Please make payment at your earliest convenience:\n{link}\n\n"
        "If you've already settled this, please ignore this message.\n\n"
        "— Recoupe"
    ),
)

# --- Firm Follow-Up (60/90-day overdue: escalated tone) ---

INVOICE_FIRM_FOLLOWUP_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "Urgent: {customer_name}, invoice {invoice_number} (INR {amount}) "
        "is {days_overdue} days past due. Immediate payment required: {link}"
    ),
)

INVOICE_FIRM_FOLLOWUP_EMAIL = MessageTemplate(
    channel="email",
    subject="URGENT: Invoice {invoice_number} — {days_overdue} Days Overdue",
    body=(
        "Hi {customer_name},\n\n"
        "Despite our previous reminder, invoice {invoice_number} for INR {amount} "
        "remains unpaid {days_overdue} days after the due date of {due_date}.\n\n"
        "We require immediate payment to avoid further escalation:\n{link}\n\n"
        "If you are experiencing difficulties, please contact us to discuss a payment plan.\n\n"
        "— Recoupe Accounts Team"
    ),
)

# --- Escalation Notice (90+ day overdue: used alongside voice call) ---

INVOICE_ESCALATION_SMS = MessageTemplate(
    channel="sms",
    subject=None,
    body=(
        "FINAL NOTICE: {customer_name}, invoice {invoice_number} (INR {amount}) "
        "is {days_overdue} days overdue. Our team will be in contact. Pay now: {link}"
    ),
)

INVOICE_ESCALATION_EMAIL = MessageTemplate(
    channel="email",
    subject="FINAL NOTICE: Invoice {invoice_number} — Escalation Required",
    body=(
        "Hi {customer_name},\n\n"
        "Invoice {invoice_number} for INR {amount} is now {days_overdue} days past due.\n\n"
        "This account has been escalated for priority follow-up. "
        "A member of our accounts team may contact you directly.\n\n"
        "To resolve this immediately:\n{link}\n\n"
        "— Recoupe Accounts Team"
    ),
)
