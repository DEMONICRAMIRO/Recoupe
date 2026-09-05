# PRD — Phase 5: Real Razorpay Adapter + Dashboard + Live Run Status

**Refer to `architecture.md` before writing any code** — specifically Section 6 (Event schema — the adapter's job is to produce this shape from Razorpay's real payloads), Section 8 (tech stack: React + Vite + TypeScript for frontend), Section 9 (Phase 5 mapping: Razorpay adapter + Dashboard), and Section 3b (WhatsApp/SMS status — the dashboard must display this honestly, not hide it).

**Design reference:** `recoupe-dashboard-mockup.html` (the click-through HTML mockup already built) is the **design and information-architecture reference only**. Its colors, layout, spacing, navigation structure, and page set (Dashboard, Audit log, Customers, 4 agent detail pages, Risk gate rules, Comms channels) are the spec to follow. Its data is 100% hardcoded placeholder (`AUDIT_ROWS`, `CUSTOMERS` arrays) — **every hardcoded array must be replaced with a real API call.** Do not ship the mockup's fake data into the real build.

**Model:** Per `model-selection.md` (rewritten after the Phase 3 Groq migration): `openai/gpt-oss-120b` for real runs, `openai/gpt-oss-20b` for test mode, via `llm_client.py`'s existing switch. Confirm with the standing "reply with exactly: PONG" sanity check before starting, per that doc's pre-flight section. Frontend scaffolding work likely needs no LLM calls at all — confirm this assumption before assigning any model to frontend tasks.

## 0. Pre-Flight — Confirm Before Writing Any Code

1. Run the Groq PONG sanity check (both models) per `model-selection.md`.
2. Re-confirm Comms Layer credentials are still valid (one real `send_sms()`, one real `send_email()`).
3. Confirm current test baseline: run `pytest backend/tests/ --collect-only` and record the real per-file counts (should be 51/26/19/46 = 142, per Phase 4's verified close — flag immediately if this has drifted).
4. Sign up for a **Razorpay test-mode account** if not already done — get test API keys (key ID + key secret) before Section 4's adapter work starts. This is the same category of pre-flight as Phase 2's Twilio/SMTP setup: don't let credential setup interrupt build momentum once started.

## 1. Objective

Two things ship this phase, both building on the fully working backend from Phases 1–4:

1. **Real Razorpay test-mode integration** for the Payment Agent branch only (per `architecture.md`'s scope — Cart/Renewal/Invoice stay on synthetic data this phase).
2. **A real React dashboard**, wired to live backend data via new read endpoints — not the hardcoded mockup — covering all 9 views from the mockup, **plus a new 10th view: Live run status**, which shows each event's current pipeline stage in real time while a batch is processing.

## 2. Scope

**In scope:**
- `backend/app/adapters/razorpay_adapter.py` — translates real Razorpay webhook/API payment-failure payloads into the internal `Event` schema (per `architecture.md` Section 6)
- Razorpay webhook endpoint wiring (`backend/app/api/routes/events.py` extended to accept real Razorpay webhooks alongside synthetic ingest)
- New read-only backend endpoints for the dashboard: batch metrics, audit log (with filters), customer list, per-agent detail, gate rules (can be a static config-reflecting endpoint since these are deterministic constants), comms channel status
- **New: live-run status** — a lightweight mechanism (in-memory store or a new lightweight table) that each agent's six-stage pipeline writes to as it processes an event, plus a polling endpoint (`GET /api/routes/live-status`) the frontend reads every 1-2 seconds while a batch is running
- Full React + Vite + TypeScript frontend implementing all 10 views (9 from the mockup + Live run), matching the mockup's visual design exactly, wired to real endpoints throughout
- Real Razorpay test-mode webhook fired at least once end-to-end (test payment failure → webhook → adapter → Event schema → Payment Agent pipeline → audit log)

**Out of scope (later phases):**
- Cart/Renewal/Invoice real API integration (still synthetic — no real Razorpay equivalent exists for these event types in your track scope)
- Dashboard write actions (approving/overriding a decision, manually triggering a retry) — this phase is read-only visibility
- Authentication/login for the dashboard (assume single-user local demo access for the buildathon)

## 3. Deliverables

1. `backend/app/adapters/razorpay_adapter.py` — payload → Event schema translation
2. Updated `backend/app/api/routes/events.py` — real webhook endpoint alongside synthetic ingest
3. `backend/app/api/routes/metrics.py` — extended for dashboard summary stats (hero figure, per-agent recovery rates, blocked count)
4. `backend/app/api/routes/audit.py` — extended with filter support (recovered/blocked/pending/sent) and customer-scoped queries
5. `backend/app/core/live_status.py` — the live-run state store (in-memory dict keyed by batch/event ID is sufficient for a hackathon demo; don't over-engineer this into a message queue)
6. `backend/app/api/routes/live_status.py` — new polling endpoint
7. `frontend/` — full React + Vite + TS app implementing all 10 views, per the mockup's design
8. `backend/tests/test_phase5.py` — covers Section 5 below

## 4. Detailed Tasks

### 4a. Razorpay adapter
- [ ] Study Razorpay's test-mode webhook payload shape for `payment.failed` events (Razorpay's own docs — fetch these, don't assume the shape)
- [ ] Map Razorpay's fields to the internal `Event` schema exactly per `architecture.md` Section 6 — `event_id`, `event_type=payment_failed`, `customer_id`, `amount`, `currency`, `reason_code` (Razorpay's decline code), `timestamp`, `metadata`
- [ ] Wire a webhook endpoint that accepts Razorpay's POST payload, runs it through the adapter, and feeds the resulting `Event` into the existing Orchestrator → Payment Agent pipeline — reuse the pipeline as-is, don't fork it for "real" vs "synthetic" events
- [ ] Trigger one real Razorpay test-mode payment failure (Razorpay's test cards support this deliberately) and confirm it flows all the way through to a real audit log entry

### 4b. Dashboard backend endpoints
- [ ] Extend `metrics.py`: return the hero "recovered this week" figure, per-agent recovery rate, events-processed count, blocked-by-gate count — computed from real `audit_log` + `events` tables, not hardcoded
- [ ] Extend `audit.py`: support `?status=recovered|blocked|pending|sent` filtering and `?customer_id=` scoping, matching the mockup's filter UI exactly
- [ ] New endpoint for customer list/detail (recovery total, contact count, reliability — pull from `customer_history`)
- [ ] Gate rules endpoint can simply reflect the hardcoded constants in `risk_gate.py` — these are deterministic and don't change per request, so this can be a near-static response; don't build unnecessary DB-backed configurability for a hackathon
- [ ] Comms channel status endpoint should reflect **actual current state**, matching `architecture.md` Section 3b — Email/SMS live, WhatsApp "sender built, delivery blocked," Voice "infrastructure confirmed, audio inconclusive." Do not let this endpoint claim WhatsApp works if it doesn't — the dashboard must be honest, including to yourself when you check it before a demo.

### 4c. Live run status (new capability)
- [ ] `live_status.py` core module: a simple in-memory structure (e.g. `dict[event_id, {stage: str, agent: str, updated_at: datetime}]`) that gets updated by each agent's pipeline as it transitions between Fetch→Enrich→Classify→Gate→Decide→Execute
- [ ] Each of the four agents' six-stage pipeline functions call a shared `update_live_status(event_id, stage)` at the start of each stage — this is a small, additive change to `payment_agent.py`, `cart_agent.py`, `renewal_agent.py`, `invoice_agent.py`, not a rewrite
- [ ] `GET /api/routes/live-status` returns the current in-memory state for all in-flight events (or the most recent batch, if nothing is currently running) — keep this simple, no need for websockets or SSE for a hackathon demo; polling is fine
- [ ] Frontend "Live run" view: a new page (not in the original mockup — build it in the same visual language) showing each in-flight event as a row with its six-stage dot-trail animating as it progresses, polling the endpoint every 1-2 seconds
- [ ] Confirm this actually works by running `scripts/run_batch.py` in one terminal while watching the Live run view update in the browser in real time — this is the actual test, not just "the endpoint returns valid JSON"

### 4d. Frontend build
- [ ] Scaffold `frontend/` per `project-structure.md`'s existing planned layout (`src/components/`, `src/pages/`, `src/api/`, `App.tsx`)
- [ ] Build all 10 views matching `recoupe-dashboard-mockup.html`'s visual design (colors, spacing, typography, component patterns) — treat the mockup as a pixel-level reference, not just a rough guide
- [ ] Replace every hardcoded data array from the mockup with a real fetch to the corresponding backend endpoint
- [ ] Confirm filters (audit log status filter) actually re-query or re-filter real data, not just toggle a CSS class on fake rows

## 5. Self-Testing Criteria

1. **Razorpay adapter correctness** — feed at least 3 sample Razorpay webhook payloads (from their docs or test-mode dashboard) through the adapter and assert the resulting `Event` matches the schema exactly, field-for-field.
2. **End-to-end real webhook test** — one real Razorpay test-mode payment failure, triggered for real, flows through webhook → adapter → Orchestrator → Payment Agent → audit log, with a real audit log row to show for it. Manually verify this in the Razorpay test dashboard and your own `audit_log` table side by side.
3. **Metrics endpoint correctness** — construct a known batch of audit log rows, call the metrics endpoint, assert the returned recovery rate / event count / blocked count match hand-calculated expected values exactly.
4. **Audit log filter correctness** — for each of the 4 filter states, assert the endpoint returns only matching rows.
5. **Live status correctness** — run a batch, poll the live-status endpoint mid-run, and assert it reflects an event genuinely mid-pipeline (not just start/end states) at least once during the test — i.e. actually catch an event at, say, the Gate stage, not just Fetch and Execute.
6. **Dashboard-backend parity** — for at least 2 of the 10 views, manually compare what the React frontend displays against a direct API call's raw response, confirming no silent transformation or stale caching.
7. **Comms channel status honesty** — assert the comms-status endpoint's WhatsApp field does NOT claim "live" delivery (must reflect the actual blocked state per Section 3b) — this is a specific regression test to prevent the dashboard from silently drifting into an inaccurate claim later.
8. **Full suite** — `pytest backend/tests/` reports 0 failures across all 5 test files, with the same test-count sanity check discipline established in Phase 4 (report per-file counts, flag any unexplained drop).

## 6. Self-Test Loop Instructions

Same discipline as Phases 1–4: fix real code on failure, never weaken a test to force a pass. The live-status feature in particular is new and easy to fake-pass (e.g. "the endpoint returns 200" without actually reflecting a real mid-pipeline state) — Criterion 5 exists specifically to catch that; don't let the test degrade into a shape-only check.

## 7. Definition of Done

- [ ] All 8 deliverables exist and are committed
- [ ] All 8 self-test criteria pass, with test-count sanity check reported explicitly
- [ ] One real Razorpay test-mode payment failure has been triggered and manually traced end-to-end (webhook → audit log)
- [ ] The React dashboard is running locally and every one of its 10 views has been manually clicked through against real data — not just asserted green by an automated test
- [ ] The Live run view has been watched updating in real time during an actual `run_batch.py` execution, screen-recorded if possible for later demo-video use
- [ ] `docs/demo-notes.md` updated with Phase 5's war stories
- [ ] `docs/architecture.md` updated to document the live-status mechanism and the new frontend structure, since this is new capability beyond what Section 9's original phase mapping described
