# PRD — Phase 4: Invoice Agent + Full System Integration Test

**Refer to `architecture.md` before writing any code** — specifically Section 4 (generic six-stage pipeline), Section 5's Invoice row (domain specifics: call-priority scoring), Section 3b (WhatsApp limitation — Invoice lists WhatsApp as a channel; real-delivery testing uses SMS + email per the binding rule), and Section 9 (Phase 4 mapping: Invoice Agent + full integration test).

**Model:** Per `model-selection.md` this phase was originally scoped for Kimi K3 via OpenCode Go. That assignment is now **stale** — Phase 3 migrated real LLM calls to Groq directly (`openai/gpt-oss-120b` for real runs, `openai/gpt-oss-20b` for test-mode under pytest, selected via `llm_client.py`'s `running_under_pytest()` switch). **Do not default back to Kimi K3 or OpenCode Go's model catalog.** Confirm which Groq model Invoice Agent's diagnosis/scoring LLM calls should use before writing code — reuse the existing dual-model switch, don't invent a third path.

## 0. Pre-Flight — Confirm Before Writing Any Code

1. **Re-confirm the LLM provider setup is still live**: run the same "reply with exactly: PONG" sanity check against both `openai/gpt-oss-120b` and `openai/gpt-oss-20b` that closed out Phase 3, since this is a new session and credentials/config could have drifted. This matters more this phase than it did in Phase 3 alone — Invoice's call-priority scoring (Section 4) now also makes real LLM calls for ambiguous cases, on top of Cart's existing diagnosis calls, so a dead LLM connection blocks two separate pieces of logic, not one.
2. **Re-confirm Comms Layer credentials are still valid** in `backend/.env` (Twilio SID/token, SMS-capable number, SMTP). These haven't changed since Phase 2, but trial-tier credentials can expire or get rate-limited — one real `send_sms()` and one real `send_email()` smoke test before starting is cheaper than discovering a stale credential mid-suite.
3. **Read `docs/demo-notes.md`'s Phase 3 voice-call entry in full**, not just the pass/fail summary. Phase 3's own reporting flagged that final audio verification stayed inconclusive even though the infrastructure passed — Invoice Agent's Decide step also escalates to voice calls (call-priority scoring), so know exactly what "voice works" currently means for this codebase before this phase leans on it further.
4. **Confirm Section 3b (WhatsApp → SMS fallback) is still the standing rule** — Invoice Agent's channel list includes WhatsApp; do not re-attempt WhatsApp business-initiated approval paths this phase without a specific new reason to believe eligibility changed.

## 1. Objective

Build the Invoice Agent as the fourth and final specialist sub-agent, adding one genuinely new piece of logic beyond what Payment/Cart/Renewal needed: a **call-priority score** that decides which overdue accounts are worth escalating to a phone call versus a lower-touch channel. Then prove the whole system works together — mixed-type batches flowing correctly through the Orchestrator to all four sub-agents in one run.

## 2. Scope

**In scope:**
- Invoice Agent, all six stages, real logic (no stubs)
- Call-priority scoring function — a new, isolated piece of logic (see Section 4)
- Full system integration test: a single mixed batch (all 4 event types) run through the Orchestrator, confirming correct routing to all four sub-agents in one pass
- Reuse of existing Comms Layer functions (`send_sms`, `send_email`, `send_whatsapp`, `place_voice_call`, `collect_feedback` where relevant) — no new sender code this phase
- New template scenario file(s) for invoice reminders/follow-ups, following the Section 3a convention

**Out of scope (later phases):**
- Real Razorpay test-mode adapter (Phase 5)
- Dashboard (Phase 5)
- Any new Comms Layer channels or providers

## 3. Deliverables

1. `backend/app/agents/invoice_agent.py` — full six-stage pipeline, replacing the Phase 1 stub
2. `backend/app/core/call_priority.py` — the call-priority scoring function, isolated from `invoice_agent.py` so it can be unit-tested independently (mirrors why `risk_gate.py` is its own file — Section 3 of `architecture.md`)
3. `backend/app/services/comms/templates/invoice_reminder.py` — scenario templates for reminder / firm follow-up / escalation notice, per the Section 3a convention (one file, one `MessageTemplate` per channel variant)
4. `backend/tests/test_phase4.py` — covers Section 5 below, including the mixed-batch integration test
5. Updated `docs/architecture.md` if Invoice's actual call-priority logic ends up differing from what Section 5's Invoice row currently describes

## 4. Detailed Tasks

- [ ] **Fetch** — pull an invoice-overdue event off the queue/table (reuse existing ingest, no changes)
- [ ] **Enrich** — DB lookup pulling account reliability, tier, and history of past broken "promise to pay" commitments (per `architecture.md` Section 5's Invoice row) — plain code, no LLM
- [ ] **Classify** — rule-based bucketing by days overdue: 30 / 60 / 90+ (deterministic, no LLM needed here — this is a clean signal, unlike Cart's inferential diagnosis)
- [ ] **Gate** — hard-coded contact rules and escalation limits, reusing the existing gate pattern; add whatever new bounded counter Invoice needs (e.g. escalation count) as an **independent counter**, not folded into Payment's or Renewal's existing caps — same principle Phase 3's call-frequency gate established
- [ ] **Call-priority scoring** (`call_priority.py`) — **hybrid logic, same pattern as Payment's Classify step (Phase 2) and Cart's Diagnose step (Phase 3):** rules handle the clear-cut cases directly (e.g. high overdue + poor reliability + broken promise = call-worthy; first-time 30-day-late reliable account = email only, no LLM needed). Call the LLM **only** when the enriched signals are genuinely ambiguous and rules alone can't confidently place the account (e.g. moderate overdue + mixed reliability history + no broken promise — where a human account manager would actually have to weigh factors, not just look up a threshold). This must stay a pure, isolated function taking (days overdue, account reliability, broken-promise history) → a priority score or tier, independent of the full agent pipeline, so it can be unit-tested in isolation before being wired into Decide. Do **not** route every case through the LLM — that defeats the point of the hybrid pattern and makes the score non-reproducible for the clear-cut majority. Follow `architecture.md`'s non-negotiable principle: rules first wherever a clean signal exists, LLM only for genuinely ambiguous cases.
- [ ] **Decide** — map (days-overdue bucket, gate result, call-priority score) → action: reminder / firm follow-up / escalate (+ voice call if priority-scored high enough)
- [ ] **Execute & Log** — render the appropriate invoice template via `templates.render()`, dispatch via the real channel (SMS/email per Section 3b; voice call for high-priority escalations), write Audit Log entry
- [ ] Build `invoice_reminder.py` templates: at minimum a reminder variant and a firm-follow-up variant, each with SMS + email `MessageTemplate` constants
- [ ] **Full system integration test**: construct one batch mixing all 4 event types (payment_failed, cart_abandoned, renewal_failed, invoice_overdue), run it through the Orchestrator, and confirm every event lands at its correct sub-agent with a corresponding audit log entry — this is the first time all four agents run together in one pass

## 5. Self-Testing Criteria

1. **Pipeline completeness** — a batch of 30 synthetic invoice-overdue events through the full pipeline produces a decision and audit log entry for every one (0 dropped).
2. **Classification correctness** — days-overdue bucketing (30/60/90+) is deterministic and 100% correct against the synthetic batch's known values (no tolerance — this is a bug if wrong, same reasoning as Payment's decline-code mapping in Phase 2).
3. **Call-priority scoring correctness** — split into two parts, mirroring how Phase 2 tested Payment's rule-vs-LLM classify step:
   - **Rule-based cases**: construct at least 3 hand-picked clear-cut cases (clearly call-worthy, clearly not, and one that should resolve via rules alone despite looking borderline) and assert the score/tier matches expectation exactly — 100% match required, no tolerance, since these are deterministic.
   - **LLM-assisted ambiguous cases**: construct at least 2 genuinely ambiguous cases where rules alone can't confidently resolve the score, confirm the LLM path is actually invoked (not silently falling back to a rule default), and manually review the reasoning for soundness rather than asserting an exact score match — same review approach Phase 2 used for Payment's ambiguous decline codes.
   Test `call_priority.py` in isolation, independent of the full agent pipeline, for both parts.
4. **Gate correctness** — at least 2 cases that should be blocked by escalation/contact limits, at least 2 that should pass, each with a logged reason. Confirm Invoice's new counter(s) are independent of Payment/Renewal/Cart's existing caps (same check Phase 3 ran for the call-frequency gate).
5. **Decision correctness** — for each (bucket, gate-result, priority-score) combination the Decide logic handles, assert the output action matches `architecture.md` Section 5's Invoice row.
6. **Comms Layer delivery** — one real `send_sms()` and one real `send_email()` in a test, confirming actual delivery status from the provider (per Section 3b — not WhatsApp). If Invoice's priority scoring triggers a voice call in the test batch, exercise `place_voice_call()` too, but treat audio-level confirmation the same cautious way Phase 3's demo-notes.md did — don't claim more certainty than you actually have.
7. **Template rendering** — `render()` correctly fills placeholders for both invoice template variants; a missing placeholder raises `KeyError`.
8. **Audit log integrity** — every processed event has exactly one audit log row, with non-null `action_taken` and `reasoning`.
9. **Full system integration test** — a single mixed batch (all 4 event types together) run through the Orchestrator: every event routes to its correct sub-agent (100% accuracy, 0 misroutes), and every event produces exactly one audit log entry regardless of which sub-agent handled it.
10. **Batch metrics** — `scripts/run_batch.py` on the mixed batch outputs a recovery rate % and ₹-recovered figure across all four event types without error.
11. **Full suite** — `pytest backend/tests/` reports 0 failures across all four phases' tests, with an explicit **test count sanity check**: report the per-file test count (test_phase1.py, test_phase2.py, test_phase3.py, test_phase4.py) and flag immediately if any prior phase's count has changed from its last known-good value, rather than only noticing at commit time.

## 6. Self-Test Loop Instructions

Same discipline as Phases 1–3: run the suite, and if anything fails, fix the actual pipeline/gate/scoring/comms/template code — never loosen a test to force a pass. Re-run until all 11 criteria are green. Log real bugs and fixes in `docs/demo-notes.md` as you go.

**Test count discipline (new this phase, given Phase 3's scare):** before declaring the suite green, explicitly diff the per-file test counts against the last committed state. If a count drops or a named test disappears, treat that as a failure requiring investigation — do not proceed to Definition of Done on an unexplained count change, even if the overall pass/fail line looks clean.

**Do not move on to Phase 5 until this loop terminates green**, including the integration test — Phase 5 builds the real Razorpay adapter directly on top of Payment Agent and assumes the whole Orchestrator routing layer is trustworthy.

## 7. Definition of Done

- [ ] All 5 deliverables exist and are committed
- [ ] All 11 self-test criteria pass
- [ ] Test-count sanity check performed and reported explicitly (not just "all passed")
- [ ] A real SMS and a real email have been sent and manually verified (check phone/inbox)
- [ ] The mixed 4-event-type integration batch has been run and its output (routing + audit log + batch metrics) manually reviewed, not just asserted green by pytest
- [ ] `docs/demo-notes.md` updated with Phase 4's real war stories, including an honest note on voice-call status if Invoice's priority scoring exercised it
- [ ] `docs/architecture.md` still accurately describes what was built — update Section 5's Invoice row if call-priority logic deviated from the plan, and confirm `model-selection.md`'s Phase 4 entry is reconciled with the actual Groq models in use (or `model-selection.md` itself updated to stop referencing dead OpenCode Go catalog IDs)
