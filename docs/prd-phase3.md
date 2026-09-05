# PRD — Phase 3: Cart Agent, Renewal Agent + Comms Layer v2

**Refer to `architecture.md` before writing any code** — Section 4 (generic pipeline), Section 5's Cart and Renewal rows, Section 3's Comms Layer entry **including the WhatsApp provider status note**, and Section 10 (non-negotiable principles). This phase directly copies Phase 2's pipeline shape — if Phase 2 passed clean, replicate its structure rather than reinventing it.

**Model:** DeepSeek V4 Pro, per `model-selection.md`. Confirm the switch before starting.

**Known constraint carried in from Phase 2:** WhatsApp delivery is provider-blocked. Every "WhatsApp" channel reference below means `send_sms()` under the hood via the existing fallback. Do not re-investigate the WhatsApp block this phase.

## 1. Objective

Build the Cart Agent and Renewal Agent as full six-stage pipelines, copying Payment Agent's proven shape. Extend the Comms Layer with two new capabilities neither Payment needed: a structured feedback-collection step, and voice-call placement with Hinglish TTS.

## 2. Scope

**In scope:**
- Cart Agent, full pipeline, including feedback collection and conditional voice-call escalation
- Renewal Agent, full pipeline, including multi-channel simultaneous notify and conditional voice-call escalation
- Comms Layer v2: `collect_feedback()` (structured quick-reply, not free-text parsing) and `place_voice_call()` (Twilio Voice + Hinglish TTS, scripted one-way message — not live conversational)
- A new Risk Gate rule: call-frequency cap, separate from message-frequency cap (a phone call is heavier-touch than a text)

**Out of scope (later phases):**
- Invoice Agent, call-priority scoring
- Live two-way conversational voice AI (explicitly a stretch goal only — see architecture.md's tiered channel plan)
- Real Razorpay test-mode API
- Dashboard

## 3. Deliverables

1. `backend/app/agents/cart_agent.py` — full pipeline, replacing the Phase 1 stub
2. `backend/app/agents/renewal_agent.py` — full pipeline, replacing the Phase 1 stub
3. `backend/app/services/comms/voice.py` — Twilio Voice + Hinglish TTS call placer
4. `backend/app/services/comms/feedback.py` — structured quick-reply collector
5. `backend/app/services/comms/templates/` — new templates: cart nudge (+ discount variant), feedback quick-reply, renewal expiry notice, voice call scripts (Hinglish) for both agents
6. `backend/app/core/risk_gate.py` — extended with the call-frequency cap rule
7. `backend/tests/test_phase3.py` — covers Section 5 below

## 4. Detailed Tasks

**Cart Agent**
- [ ] Fetch → Enrich (purchase history, price sensitivity) → Diagnose (LLM scores likely abandonment reason: price / timeout / distraction — no clean rule-based signal exists here, this is the genuine judgment node)
- [ ] Gate → Decide: nudge type (+ discount tier if price-flagged)
- [ ] Execute: dispatch nudge via WhatsApp-fallback-to-SMS + Email
- [ ] New: `collect_feedback()` — send a structured quick-reply ("Too expensive / Payment issue / Just browsing / Other") after the nudge
- [ ] New: if feedback indicates an unresolved blocker (e.g. "Payment issue"), trigger `place_voice_call()` with a Hinglish script offering help
- [ ] Log every step, including the feedback reason captured (this feeds a dashboard metric later — "top reasons for cart abandonment")

**Renewal Agent**
- [ ] Fetch → Enrich (tenure, past failures, LTV) → Diagnose (rules: card-expiry vs funds vs processor error)
- [ ] Gate (grace period rules, retry limit, **new** call-frequency cap) → Decide: silent retry / prompt card update / grace period
- [ ] Execute: dispatch expiry notification across SMS + Email + WhatsApp-fallback **simultaneously** (not picking one channel — all three)
- [ ] New: escalate to `place_voice_call()` if customer is high-value (LTV threshold — define one) or unresponsive after N days
- [ ] Log every step

**Comms Layer v2**
- [ ] `voice.py`: place a real Twilio Voice call, play a TTS-generated Hinglish script (one-way, scripted — not interactive)
- [ ] `feedback.py`: send structured quick-reply options, capture the selected reason, write it to the event's audit context
- [ ] Extend `risk_gate.py` with a call-frequency cap distinct from the existing message-frequency cap

## 5. Self-Testing Criteria

1. **Pipeline completeness** — a batch of 30 synthetic cart-abandonment events and 30 renewal-failure events each produce a decision and audit entry for every event, 0 dropped.
2. **Cart diagnosis sanity** — manually review 5 LLM-scored abandonment reasons against the underlying behavioral signals; confirm reasoning is plausible (this node has no ground truth to check against exactly, so this is a judgment review, not an exact-match test).
3. **Renewal diagnosis correctness** — rule-based cause classification (card-expiry / funds / processor) is 100% correct against labeled synthetic test cases.
4. **Gate correctness, call-frequency cap** — construct a test case where a customer already received a call this week; confirm the gate blocks a second call but still allows a text-based nudge (the two caps are independent).
5. **Multi-channel simultaneity** — for a Renewal test case, confirm all three channels (SMS, Email, WhatsApp-fallback) were actually dispatched for the same event, not just one.
6. **Feedback loop** — trigger `collect_feedback()` in a test, simulate a "Payment issue" response, confirm it correctly triggers `place_voice_call()`; simulate a "Just browsing" response, confirm it does NOT trigger a call.
7. **Voice call delivery** — place one real Twilio Voice call in a test, confirm the call connects and the Hinglish TTS script plays (manually verify by receiving the call).
8. **Audit integrity** — every processed event across both agents has a complete audit trail, including the feedback reason where applicable.
9. **Full suite** — `pytest backend/tests/` reports 0 failures, including all Phase 1 and Phase 2 tests (0 regressions).

## 6. Self-Test Loop Instructions

Same protocol as Phases 1–2: run the suite, fix real code on failure, never weaken a test, repeat until all 9 criteria are green. Log real bugs and fixes in `docs/demo-notes.md` as they happen.

**Do not move to Phase 4 until this loop terminates green.** Invoice Agent in Phase 4 will need the call-frequency gate logic and voice-call function working correctly, since it adds call-priority scoring on top of them.

## 7. Definition of Done

- [ ] All 7 deliverables committed
- [ ] All 9 self-test criteria pass
- [ ] One real voice call placed and manually verified (you received it, script played correctly)
- [ ] `docs/demo-notes.md` updated with Phase 3's real war stories
- [ ] `docs/architecture.md` still accurately reflects what was built — update it if anything deviated, especially if the WhatsApp provider status changes
