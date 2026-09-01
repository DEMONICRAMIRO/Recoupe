# PRD — Phase 2: Payment Agent + Comms Layer v1

**Refer to `architecture.md` before writing any code** — specifically Section 4 (generic sub-agent pipeline), Section 5's Payment row (domain specifics), Section 3's Comms Layer entry, and the new **Template Convention** sub-note below (add it to `architecture.md` Section 3 once agreed). This phase is the template every later sub-agent copies, so getting the shape right here matters more than in any other phase.

**Model:** GPT 5.6 Luna, per `model-selection.md`. Confirm the switch before starting.

## 0. Pre-Flight — Do This Before Writing Any Code

The self-test loop (Section 6) must fail only on real code bugs, never on missing infrastructure. Provision these first:

**Twilio Sandbox (WhatsApp + SMS):**
1. Create a Twilio trial account — gives a trial number + starter credits.
2. Console → Messaging → Try it out → **WhatsApp Sandbox** — gives a sandbox number and a join code (e.g. "join happy-tiger"). Send that phrase from your own WhatsApp to the sandbox number to activate it. This is the "pre-registered test number" this phase's demo relies on.
3. SMS on a trial account can generally only send to **verified** numbers — verify your own phone under Console → Verified Caller IDs.
4. Collect: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (sandbox number, format `whatsapp:+14155238886`), `TWILIO_SMS_FROM` (trial number).

**SMTP (email):**
- Fastest path: Gmail SMTP with an **App Password** (Google Account → Security → 2-Step Verification → App Passwords) — do not use your real account password.
- Collect: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASS`.

**`.env.example`** (commit this; keep real values only in the gitignored `.env`):
```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_SMS_FROM=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
```

**Do not start Section 4's tasks until every value above is filled in `.env` and you've confirmed you can log into the Twilio console and see the sandbox as active.**

## 1. Objective

Build the Payment Agent as a fully real, end-to-end six-stage pipeline (Fetch → Enrich → Classify → Gate → Decide → Execute & Log), and build the first version of the shared Comms Layer alongside it, since Payment's "send payment link" action is the first thing in the whole system that needs to actually send a message.

## 2. Scope

**In scope:**
- Payment Agent, all six stages, real logic (no stubs left over from Phase 1)
- Risk & Compliance Gate as plain deterministic code — max retries, cooldown window
- Comms Layer v1: `send_email()`, `send_sms()`, `send_whatsapp()` (voice call comes in Phase 3)
- Real Twilio Sandbox + SMTP wiring (not mocked — this should actually send)
- Templates package: shared loader contract + one payment-link scenario file (see Section 3a)

**Out of scope (later phases):**
- Cart, Renewal, Invoice agents
- Voice calls, feedback collection
- Real Razorpay test-mode API (still synthetic data this phase)
- Dashboard

## 3. Deliverables

1. `backend/app/agents/payment_agent.py` — full pipeline, replacing the Phase 1 stub
2. `backend/app/core/risk_gate.py` — real gate logic (was a placeholder in Phase 1)
3. `backend/app/services/comms/email.py`, `sms.py`, `whatsapp.py` — real senders
4. `backend/app/services/comms/templates/__init__.py` — shared `MessageTemplate` dataclass + `render()` loader (see Section 3a)
5. `backend/app/services/comms/templates/payment_link.py` — payment-link scenario templates (SMS + WhatsApp variants)
6. `backend/tests/test_phase2.py` — covers Section 5 below

### 3a. Template Convention (locked in for this phase, binding on Phases 3–4)

This resolves what was previously an ambiguous "template... or equivalent" deliverable. The convention, once built here, is not to be reinvented in later phases:

- **One file per scenario**, not per agent — e.g. `payment_link.py`, later `cart_nudge.py`, `renewal_notice.py`, `invoice_reminder.py`. An agent may need several scenario files as its logic grows.
- Each scenario file exports one `MessageTemplate` constant **per channel variant** it supports (e.g. `PAYMENT_LINK_SMS`, `PAYMENT_LINK_WHATSAPP`).
- `templates/__init__.py` defines the shared contract:
  ```python
  from dataclasses import dataclass

  @dataclass
  class MessageTemplate:
      channel: str            # "email" | "sms" | "whatsapp" | "voice"
      subject: str | None     # only used for email
      body: str               # may contain {placeholders}

  def render(template: MessageTemplate, **kwargs) -> MessageTemplate:
      """Fill placeholders in subject/body. Raises KeyError on missing keys."""
      rendered_body = template.body.format(**kwargs)
      rendered_subject = template.subject.format(**kwargs) if template.subject else None
      return MessageTemplate(channel=template.channel, subject=rendered_subject, body=rendered_body)
  ```
- **Rendering happens in the agent's Execute step**, using `render()` — never inside `comms/email.py` / `sms.py` / `whatsapp.py`. Those three files stay dumb senders: they accept an already-rendered string/subject and dispatch it. This keeps the Comms Layer domain-agnostic, which matters once four agents are calling into it.
- `payment_link.py` for this phase needs `PAYMENT_LINK_SMS` and `PAYMENT_LINK_WHATSAPP`, each with `{customer_name}`, `{amount}`, and `{link}` placeholders.

## 4. Detailed Tasks

- [ ] **Fetch** — pull a payment-failure event off the queue/table (reuse Phase 1's ingest, no changes needed here)
- [ ] **Enrich** — DB lookup joining `customer_history` onto the event (plain code, no LLM — per `architecture.md`'s non-negotiable principle)
- [ ] **Classify** — rule-based mapping of decline code → cause (insufficient funds, bank timeout, expired card, 3DS failure, network issue); call the LLM only when the enriched history makes a code genuinely ambiguous (e.g., same code failing repeatedly — is it a dead card or a temporary bank issue?)
- [ ] **Gate** — hard-coded checks: block if `contact_count_7d` exceeds the cap, block if inside the cooldown window since the last retry; every gate decision (allow or block) gets a reason string
- [ ] **Decide** — map (cause, gate result) → action: retry now / retry with alt route / send payment link / hold
- [ ] **Execute & Log** — perform the mock retry (or call Comms Layer if the decision is "send payment link"), render the payment-link template via `templates.render()`, write an Audit Log entry with action + reasoning
- [ ] Build Comms Layer functions with real Twilio Sandbox (WhatsApp/SMS) and real SMTP (email) credentials in `.env` (Section 0 must be complete before this task starts)
- [ ] Build `templates/__init__.py` loader and `templates/payment_link.py` per Section 3a

## 5. Self-Testing Criteria

1. **Pipeline completeness** — running a batch of 30 synthetic payment-failure events through the full pipeline produces a decision and an audit log entry for every single one (0 events silently dropped).
2. **Classification correctness** — for the rule-based decline codes, 100% match the expected cause mapping (this is deterministic, so anything less than 100% is a bug, not a tolerance). For LLM-assisted ambiguous cases, manually review a sample of 5 and confirm the reasoning is sound.
3. **Gate correctness** — construct at least 2 test cases that should be blocked (e.g., a customer with 3 retries in the last 7 days) and confirm the gate blocks both, with a logged reason. Construct at least 2 cases that should pass and confirm both are allowed.
4. **Decision correctness** — for each (cause, gate-result) combination your Decide logic handles, assert the output action matches what's documented in `architecture.md` Section 5's Payment row.
5. **Comms Layer delivery (scope revision)** — trigger one real `send_sms()` and one real `send_email()` call in a test; confirm both return a success status from Twilio/SMTP (not just "didn't throw an exception" — actually check the delivery confirmation). WhatsApp delivery was attempted separately with the account's configured sender and Twilio's official Appointment Reminder Content SID; both were rejected by this trial account's WhatsApp eligibility. The WhatsApp result and exact provider errors are recorded in `docs/demo-notes.md`.
6. **Template rendering** — `render()` correctly fills `{customer_name}`, `{amount}`, `{link}` for both `PAYMENT_LINK_SMS` and `PAYMENT_LINK_WHATSAPP`; a missing placeholder raises `KeyError` rather than silently sending a broken message.
7. **Audit log integrity** — every processed event has exactly one corresponding audit log row, with non-null `action_taken` and `reasoning` fields.
8. **Batch metrics** — `scripts/run_batch.py` on the 30-event batch outputs a recovery rate % and ₹-recovered figure without error.
9. **Full suite** — `pytest backend/tests/` reports 0 failures, including Phase 1's existing tests (nothing regressed).

## 6. Self-Test Loop Instructions

Same discipline as Phase 1: run the suite, and if anything fails, fix the actual pipeline/gate/comms/template code — never loosen a test to force a pass. The one exception is Criterion 5: a failure there may be a credentials/infra gap (see Section 0), not a code bug — check `.env` and provider status first, then treat any remaining failure as code. For this account, WhatsApp was explicitly attempted and the criterion was revised to the provider-supported real SMS path plus SMTP email; both must return success. Re-run until all 9 criteria are green. Log any real bugs you hit and how you fixed them in `docs/demo-notes.md` as you go, not retroactively.

**Do not move on to Phase 3 until this loop terminates green.** Phase 3 (Cart + Renewal agents) directly copies this phase's pipeline shape *and* its template convention — any looseness here becomes three separate bugs later instead of one.

## 7. Definition of Done

- [ ] All 6 deliverables exist and are committed
- [ ] All 9 self-test criteria pass
- [ ] A real SMS message and a real email have been sent and manually verified (check your own phone/inbox)
- [ ] `docs/demo-notes.md` updated with Phase 2's real war stories
- [ ] `docs/architecture.md` still accurately describes what was built — if you deviated from it, update the doc, don't leave it stale, and specifically confirm the Template Convention (Section 3a) has been added to `architecture.md` Section 3 so Phase 3 inherits it automatically
